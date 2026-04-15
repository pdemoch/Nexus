import polars as pl

class NexusTransformer:
    # Atenção: Agora recebe e devolve LazyFrames (lf_)
    def processar_camada_silver(self, lf_150: pl.LazyFrame, lf_188: pl.LazyFrame, df_seg: pl.DataFrame) -> pl.LazyFrame:
        print("\n⚙️ [SILVER] Harmonizando dados e construindo Star Schema (Out-of-Core)...")

        lf_vendas = lf_150.filter(
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

        lf_clientes = lf_188.select([
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
        ]).unique(subset=["cod_loja"], keep="first")
            
        lf_vendas = lf_vendas.join(
            lf_clientes, left_on="cliente_loja", right_on="cod_loja", how="left"
        )

        lf_vendas = lf_vendas.with_columns([
            pl.when(pl.col("supervisor_nome").is_null() | (pl.col("supervisor_nome").str.strip_chars() == ""))
            .then(pl.col("gerente_nome"))
            .otherwise(pl.col("supervisor_nome")).alias("supervisor_nome"),
            pl.col("cgc").fill_null("SEM_CGC"),
            pl.col("cliente_razaosocial").fill_null("SEM_NOME"),
            pl.col("bloqueado").fill_null("ATIVO"),
            pl.col("vendedor_nome").fill_null("SEM VENDEDOR")
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

            # REGRA DE NEGÓCIO MANTIDA: INNER JOIN APAGA O HISTÓRICO DE INATIVOS
            lf_silver = lf_vendas.join(
                df_portfolio.lazy().select(["produto", "bu", "categoria", "segmento", "2026"]),
                on="produto",
                how="inner"
            )
            
            lf_silver = lf_silver.with_columns([
                pl.col("bu").fill_null("DESCONHECIDO"),
                pl.col("categoria").fill_null("DESCONHECIDO"),
                pl.col("segmento").fill_null("DESCONHECIDO"),
                pl.col("2026").fill_null("ATIVO")
            ])
        else:
            return pl.LazyFrame()

        lf_silver = lf_silver.with_columns(pl.col("cliente").alias("cod_cliente"))
        
        colunas_finais = [
            "dtapedido", "pedido", "cod_cliente", "loja", "cgc", "cliente_razaosocial", 
            "regional", "produto", "descricao", "qtpedido", "vlpedido", 
            "vendedor_nome", "gerente_nome", "supervisor_nome", "bu", "categoria", "segmento",
            "bloqueado"
        ]
        
        lf_final = lf_silver.select(colunas_finais + [pl.col("2026").alias("curva_2026")])
        return lf_final
    
    def preparar_camada_ia(self, df_silver: pl.DataFrame) -> pl.DataFrame:
        print("\n🧠 [GOLD/IA] Preparando base nativa para IA (Sem Pandas)...")
        if df_silver.is_empty(): return pl.DataFrame()

        # OOM FIX: Operações puras no Polars, 10x mais rápido que o pandas e gasta pouca RAM
        df_ia = df_silver.with_columns([
            pl.col("dtapedido").cast(pl.Utf8).str.strptime(pl.Date, "%Y%m%d", strict=False)
        ]).drop_nulls("dtapedido")

        df_ia = df_ia.with_columns([
            pl.col("dtapedido").dt.strftime("%Y-%m").alias("mes_ano")
        ])

        df_ia = df_ia.group_by([
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