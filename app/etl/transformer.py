import polars as pl

class NexusTransformer:
    def processar_camada_silver(self, lf_150: pl.LazyFrame, lf_188: pl.LazyFrame, df_seg: pl.DataFrame) -> pl.LazyFrame:
        print("\n⚙️ [SILVER] Harmonizando dados para Injeção no Banco (Upsert)...")

        # 1. Tratamento da Tabela de Vendas (150)
        lf_vendas = lf_150.filter(
            (pl.col("operacao").cast(pl.Utf8).str.strip_chars() != "51") & 
            (~pl.col("regional").cast(pl.Utf8).str.to_uppercase().str.contains("FIFEIRO")) &
            (~pl.col("regional").cast(pl.Utf8).str.to_uppercase().str.contains("EIC"))
        ).with_columns([
            pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("cliente").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("descricao").cast(pl.Utf8).str.strip_chars(),
            # BLINDAGEM DO PEDIDO: Se o ERP mandar vazio, cria um identificador genérico para não quebrar o banco
            pl.col("pedido").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars().fill_null("S/N") 
        ]).with_columns([
            pl.concat_str([pl.col("cliente"), pl.lit("_"), pl.col("loja")]).alias("cliente_loja")
        ])

        # 2. Tratamento do Cadastro de Clientes (188)
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
            
        # 3. Cruzamento Vendas x Clientes
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
            return pl.LazyFrame()

        lf_silver = lf_silver.with_columns(pl.col("cliente").alias("cod_cliente"))
        
        # ATUALIZAÇÃO S&OE: Adicionadas as colunas 'qtfatura' e 'qtcorte' para garantir
        # que o saldo ativo do pedido possa ser calculado!
        colunas_finais = [
            "dtapedido", "pedido", "cod_cliente", "loja", "cgc", "cliente_razaosocial", 
            "regional", "produto", "descricao", "qtpedido", "vlpedido", "qtfatura", "qtcorte",
            "vendedor_nome", "gerente_nome", "supervisor_nome", "bu", "categoria", "segmento",
            "bloqueado"
        ]
        
        lf_final = lf_silver.select(colunas_finais + [pl.col("2026").alias("curva_2026")])
        
        # BLINDAGEM FINAL PARA O UPSERT: Garante que não há linhas duplicadas no mesmo lote delta
        # para a mesma chave (pedido + produto + cgc), evitando erros no PostgreSQL.
        lf_final = lf_final.unique(subset=["pedido", "produto", "cgc"], keep="last")
        
        return lf_final