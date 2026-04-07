import polars as pl
import pandas as pd

class NexusTransformer:
    def processar_camada_silver(self, df_150: pl.DataFrame, df_188: pl.DataFrame, df_seg: pl.DataFrame) -> pl.DataFrame:
        print("\n⚙️ [SILVER] Harmonizando dados e construindo Star Schema...")

        if df_150.is_empty():
            return pl.DataFrame()

        df_vendas = df_150.filter(
            (pl.col("operacao").cast(pl.Utf8).str.strip_chars() != "51") & 
            (~pl.col("regional").cast(pl.Utf8).str.to_uppercase().str.contains("FIFEIRO")) &
            (~pl.col("regional").cast(pl.Utf8).str.to_uppercase().str.contains("EIC"))
        ).with_columns([
            pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("cliente").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("descricao").cast(pl.Utf8).str.strip_chars()
        ]).with_columns([
            pl.concat_str([pl.col("cliente"), pl.lit("_"), pl.col("loja")]).alias("cliente_loja")
        ])

        if not df_188.is_empty():
            df_clientes = df_188.select([
                "cod", "loja", "cgc", "razao social", "bloqueado", "vendedor_nome", "gerente_nome", "supervisor_nome"
            ]).rename({
                "razao social": "cliente_razaosocial" 
            }).with_columns([
                pl.col("cod").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                pl.col("cgc").cast(pl.Utf8).str.strip_chars(),
                pl.col("cliente_razaosocial").cast(pl.Utf8).str.strip_chars(),
                pl.col("gerente_nome").cast(pl.Utf8).fill_null("SEM GERENTE").str.to_uppercase().str.strip_chars()
            ]).with_columns([
                pl.concat_str([pl.col("cod"), pl.lit("_"), pl.col("loja")]).alias("cod_loja")
            ])
            
            df_clientes = df_clientes.unique(subset=["cod_loja"], keep="first")
            
            df_vendas = df_vendas.join(
                df_clientes, left_on="cliente_loja", right_on="cod_loja", how="left"
            )

            df_vendas = df_vendas.with_columns(
                pl.when(pl.col("supervisor_nome").is_null() | (pl.col("supervisor_nome").str.strip_chars() == ""))
                .then(pl.col("gerente_nome"))
                .otherwise(pl.col("supervisor_nome")).alias("supervisor_nome")
            )
        else:
            df_vendas = df_vendas.with_columns([
                pl.lit("SEM_CGC").alias("cgc"),
                pl.lit("SEM_NOME").alias("cliente_razaosocial"),
                pl.lit("ATIVO").alias("bloqueado"),
                pl.lit(None).alias("vendedor_nome"),
                pl.lit("SEM GERENTE").alias("gerente_nome"),
                pl.lit(None).alias("supervisor_nome")
            ])

        if not df_seg.is_empty():
            df_portfolio = df_seg.with_columns([
                pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                pl.col("2026").cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
            ]).filter(
                (pl.col("2026").str.contains(r"^[A-Z]{1,2}$")) | 
                (pl.col("2026") == "LANÇAMENTO")
            ).unique(subset=["produto"], keep="first")

            print(f"🎯 [SILVER] Portfólio rigorosamente filtrado para {df_portfolio.height} SKUs ativos.")

            df_silver = df_vendas.join(
                df_portfolio.select(["produto", "bu", "categoria", "segmento", "2026"]),
                on="produto",
                how="inner"
            )
            
            df_silver = df_silver.with_columns([
                pl.col("bu").fill_null("DESCONHECIDO"),
                pl.col("categoria").fill_null("DESCONHECIDO"),
                pl.col("segmento").fill_null("DESCONHECIDO"),
                pl.col("2026").fill_null("ATIVO")
            ])
        else:
            return pl.DataFrame()

        df_silver = df_silver.with_columns(pl.col("cliente").alias("cod_cliente"))
        
        colunas_finais = [
            "dtapedido", "pedido", "cod_cliente", "loja", "cgc", "cliente_razaosocial", 
            "regional", "produto", "descricao", "qtpedido", "vlpedido", 
            "vendedor_nome", "gerente_nome", "supervisor_nome", "bu", "categoria", "segmento",
            "bloqueado"
        ]
        
        df_final = df_silver.select(colunas_finais + [pl.col("2026").alias("curva_2026")])

        print(f"✅ [SILVER] Harmonização concluída! Base de Vendas ativa: {df_final.height} registros.")
        return df_final
    
    def preparar_camada_ia(self, df_silver: pl.DataFrame) -> pl.DataFrame:
        print("\n🧠 [GOLD/IA] Preparando base para IA...")
        if df_silver.is_empty(): return pl.DataFrame()

        df_pd = df_silver.to_pandas()
        df_pd['dtapedido'] = pd.to_datetime(df_pd['dtapedido'], format='%Y%m%d', errors='coerce')
        df_pd = df_pd.dropna(subset=['dtapedido'])
        df_pd['mes_ano'] = df_pd['dtapedido'].dt.strftime('%Y-%m')
        
        df_agrupamento = pl.from_pandas(df_pd)

        df_ia = df_agrupamento.group_by([
            "mes_ano", "produto", "descricao", "bu", "categoria", "segmento", "curva_2026"
        ]).agg([
            pl.col("qtpedido").cast(pl.Float64).sum().alias("total_qtpedido"),
            pl.col("vlpedido").cast(pl.Float64).sum().alias("total_vlpedido")
        ])

        df_ia = df_ia.with_columns(
            pl.when(pl.col("total_qtpedido") == 0)
            .then(0.0)
            .otherwise(pl.col("total_vlpedido") / pl.col("total_qtpedido"))
            .alias("pmv")
        ).sort(["produto", "mes_ano"])

        print(f"🎯 [GOLD/IA] Base agregada gerada para {df_ia.select('produto').n_unique()} SKUs ativos reais.")
        return df_ia