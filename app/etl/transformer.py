import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta

class NexusTransformer:
    def processar_camada_silver(self, lf_150: pl.LazyFrame, lf_188: pl.LazyFrame, df_seg: pl.DataFrame, df_orc: pl.DataFrame):
        print("\n⚙️ [SILVER] Harmonizando dados (Filtro Cerca-Viva: Janela Móvel)...")

        # =========================================================================
        # 🔥 O FILTRO CERCA-VIVA (BARREIRA CONTRA HISTÓRICO ANTIGO)
        # Calcula a mesma janela do pipeline para garantir que apenas os últimos
        # 3 meses sejam processados e enviados para o Loader.
        # =========================================================================
        hoje = date.today()
        data_inicio_janela = (hoje - relativedelta(months=3)).replace(day=1)
        # Converte para string YYYYMMDD para comparar com a coluna 'dtapedido' da 150
        data_inicio_str = data_inicio_janela.strftime("%Y%m%d")

        # 1. Tratamento da Tabela de Vendas (150)
        lf_vendas = lf_150.filter(
            (pl.col("dtapedido") >= data_inicio_str) &  # <-- BLINDAGEM TEMPORAL
            (pl.col("operacao").cast(pl.Utf8).str.strip_chars() != "51") & 
            (~pl.col("regional").cast(pl.Utf8).str.to_uppercase().str.contains("FIFEIRO")) &
            (~pl.col("regional").cast(pl.Utf8).str.to_uppercase().str.contains("EIC"))
        ).with_columns([
            pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("cliente").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("descricao").cast(pl.Utf8).str.strip_chars(),
            pl.col("pedido").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars().fill_null("S/N") 
        ]).with_columns([
            pl.concat_str([pl.col("cliente"), pl.lit("_"), pl.col("loja")]).alias("cliente_loja")
        ])
        
        # --- GARANTIA FINANCEIRA: Adicionando colunas de valores reais com fallback 0 se vier nulo ---
        colunas_disponiveis = lf_vendas.columns
        for col_fin in ["qtfatura", "vlfatura", "qtcorte", "vlcorte"]:
            if col_fin not in colunas_disponiveis:
                lf_vendas = lf_vendas.with_columns(pl.lit(0.0).alias(col_fin))
            else:
                lf_vendas = lf_vendas.with_columns(pl.col(col_fin).cast(pl.Float64).fill_null(0.0))

        colunas_vendas = lf_vendas.columns
        colunas_conflito = ["vendedor_nome", "gerente_nome", "supervisor_nome", "cgc", "razao social", "cliente_razaosocial", "bloqueado"]
        col_remover = [c for c in colunas_conflito if c in colunas_vendas]
        if col_remover:
            lf_vendas = lf_vendas.drop(col_remover)

        # 2. Tratamento do Cadastro de Clientes (188)
        # Nomes de coluna corrigidos cirurgicamente ('cod' e 'razao social')
        lf_clientes = lf_188.select([
            "cod", "loja", "cgc", "razao social", "bloqueado", "vendedor_nome", "gerente_nome", "supervisor_nome"
        ]).rename({
            "razao social": "cliente_razaosocial" 
        }).with_columns([
            pl.col("cod").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("cgc").cast(pl.Utf8).str.strip_chars(),
            pl.col("cliente_razaosocial").cast(pl.Utf8).str.strip_chars(),
            pl.col("bloqueado").cast(pl.Utf8).str.to_uppercase().str.strip_chars(), 
            pl.col("vendedor_nome").cast(pl.Utf8).fill_null("SEM VENDEDOR").str.to_uppercase().str.strip_chars(),
            pl.col("gerente_nome").cast(pl.Utf8).fill_null("SEM GERENTE").str.to_uppercase().str.strip_chars(),
            pl.col("supervisor_nome").cast(pl.Utf8).str.to_uppercase().str.strip_chars()
        ])

        lf_clientes = lf_clientes.with_columns([
            pl.when(
                pl.col("supervisor_nome").is_null() | 
                (pl.col("supervisor_nome") == "") | 
                (pl.col("supervisor_nome") == "NULL") | 
                (pl.col("supervisor_nome") == "NAN")
            )
            .then(pl.col("gerente_nome"))
            .otherwise(pl.col("supervisor_nome")).alias("supervisor_nome")
        ]).with_columns([
            pl.concat_str([pl.col("cod"), pl.lit("_"), pl.col("loja")]).alias("cod_loja")
        ]).unique(subset=["cod_loja"], keep="first")
            
        # 3. Cruzamento Vendas x Clientes
        lf_vendas = lf_vendas.join(
            lf_clientes, left_on="cliente_loja", right_on="cod_loja", how="left"
        ).with_columns([
            pl.col("cgc").fill_null("SEM_CGC"),
            pl.col("cliente_razaosocial").fill_null("SEM_NOME"),
            pl.col("bloqueado").fill_null("ATIVO"),
            pl.col("supervisor_nome").fill_null("SEM SUPERVISOR"),
            pl.col("gerente_nome").fill_null("SEM GERENTE"),
            pl.col("vendedor_nome").fill_null("SEM VENDEDOR")
        ])

        # 4. Filtro Rígido de Portfólio (Segmentos.xlsx)
        if not df_seg.is_empty():
            df_portfolio = df_seg.with_columns([
                pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                pl.col("2026").cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
            ]).filter(
                (pl.col("2026").str.contains(r"^[A-Z]{1,2}$")) | 
                (pl.col("2026") == "LANÇAMENTO")
            ).unique(subset=["produto"], keep="first")

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
            return None, None, None

        lf_silver = lf_silver.with_columns(pl.col("cliente").alias("cod_cliente"))
        lf_final = lf_silver.with_columns(pl.col("2026").alias("curva_2026"))
        
        # --- A GRANDE CORREÇÃO (O FUNIL): Agora os Reais (R$) passam aqui! ---
        lf_final = lf_final.group_by(["pedido", "produto", "cgc"]).agg([
            pl.col("qtpedido").sum().alias("qtpedido"),
            pl.col("vlpedido").sum().alias("vlpedido"),
            pl.col("qtfatura").sum().alias("qtfatura"),
            pl.col("qtcorte").sum().alias("qtcorte"),
            pl.col("vlfatura").sum().alias("vlfatura"), # -> Nova Coluna Passando
            pl.col("vlcorte").sum().alias("vlcorte"),   # -> Nova Coluna Passando
            pl.col("dtapedido").first().alias("dtapedido"),
            pl.col("cod_cliente").first().alias("cod_cliente"),
            pl.col("loja").first().alias("loja"),
            pl.col("cliente_razaosocial").first().alias("cliente_razaosocial"),
            pl.col("regional").first().alias("regional"),
            pl.col("descricao").first().alias("descricao"),
            pl.col("vendedor_nome").first().alias("vendedor_nome"),
            pl.col("gerente_nome").first().alias("gerente_nome"),
            pl.col("supervisor_nome").first().alias("supervisor_nome"),
            pl.col("bu").first().alias("bu"),
            pl.col("categoria").first().alias("categoria"),
            pl.col("segmento").first().alias("segmento"),
            pl.col("bloqueado").first().alias("bloqueado"),
            pl.col("curva_2026").first().alias("curva_2026")
        ])

        # 5. Transformação do Orçamento (Blindada e Agrupada)
        df_orc_final = pl.DataFrame()
        if not df_orc.is_empty():
            df_orc = df_orc.filter(pl.col("Produto").is_not_null())
            df_orc = df_orc.with_columns(pl.col("Produto").str.strip_chars())
            
            meses = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
            meses_map = {'jan':'01','fev':'02','mar':'03','abr':'04','mai':'05','jun':'06','jul':'07','ago':'08','set':'09','out':'10','nov':'11','dez':'12'}
            
            meses_existentes = [m for m in meses if m in df_orc.columns]
            
            if meses_existentes:
                df_melted = df_orc.melt(
                    id_vars=["Produto"], 
                    value_vars=meses_existentes, 
                    variable_name="mes_str", 
                    value_name="receita_orcamento"
                )
                
                try:
                    expr_mes = pl.col("mes_str").replace(meses_map).alias("mes_num")
                except AttributeError:
                    expr_mes = pl.col("mes_str").map_dict(meses_map).alias("mes_num")
                    
                df_orc_final = df_melted.with_columns([
                    expr_mes
                ]).with_columns([
                    pl.format("2026-{}-01", pl.col("mes_num")).str.strptime(pl.Date, "%Y-%m-%d").alias("mes_projetado"),
                    pl.col("receita_orcamento").cast(pl.Float64)
                ]).rename({"Produto": "sku"})
                
                # CORREÇÃO CRÍTICA: Somatória agrupada para evitar sobrescrita de SKUs repetidos
                df_orc_final = df_orc_final.group_by(["sku", "mes_projetado"]).agg([
                    pl.col("receita_orcamento").sum().alias("receita_orcamento")
                ]).select(["sku", "mes_projetado", "receita_orcamento"])
                
        lf_final = lf_final.rename({"produto": "sku"})
        return lf_final, lf_clientes, df_orc_final