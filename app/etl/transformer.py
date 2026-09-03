import polars as pl
from datetime import date
from dateutil.relativedelta import relativedelta

# Fallback usado APENAS se o pipeline não passar a data. O valor que vale é o
# do pipeline (app/etl/pipeline.py -> JANELA_MESES).
JANELA_MESES = 5

class NexusTransformer:
    def _carregar_de_para(self) -> pl.DataFrame:
        """
        Mapa de SKU origem -> destino, lido da dim_de_para_sku a cada rodada.

        POR QUE ISTO EXISTE NA CARGA E NAO COMO UPDATE AVULSO:
        executar_carga_silver roda DELETE FROM fato_vendas WHERE data_pedido >= :dt
        e reinsere o que veio do ERP. A janela e de 3 meses. Um UPDATE manual em
        fato_vendas seria apagado na proxima rodada, porque o ERP continua
        mandando o codigo de origem. O mapa precisa ser aplicado TODA VEZ, aqui.

        Incluir um par novo passa a ser um INSERT na tabela, sem deploy.
        Se a tabela ainda nao existir, devolve vazio e a carga segue como antes.
        """
        try:
            from sqlalchemy import text
            from app.core.database import SessionLocal
            with SessionLocal() as db:
                linhas = db.execute(text("""
                    SELECT sku_origem, sku_destino, descricao_destino
                    FROM dim_de_para_sku WHERE ativo
                """)).fetchall()
            if not linhas:
                return pl.DataFrame()
            return pl.DataFrame({
                "produto":           [str(r[0]).strip() for r in linhas],
                "produto_destino":   [str(r[1]).strip() for r in linhas],
                "descricao_destino": [r[2] for r in linhas],
            })
        except Exception as e:
            print(f"⚠️ [SILVER] dim_de_para_sku indisponível ({e}). Seguindo sem DE-PARA.")
            return pl.DataFrame()

    def processar_camada_silver(self, lf_150: pl.LazyFrame, lf_188: pl.LazyFrame,
                                df_seg: pl.DataFrame, df_orc: pl.DataFrame,
                                data_inicio_janela=None):
        print("\n⚙️ [SILVER] Harmonizando dados (Filtro Cerca-Viva: Janela Móvel)...")

        # =========================================================================
        # 🔥 O FILTRO CERCA-VIVA (BARREIRA CONTRA HISTÓRICO ANTIGO)
        #
        # A DATA VEM DO PIPELINE, não é mais recalculada aqui.
        #
        # POR QUE: antes esta janela era calculada de forma INDEPENDENTE da do
        # pipeline, com um segundo `months=3` escrito neste arquivo. O loader
        # apaga a partir da data do PIPELINE.
        # (DELETE FROM fato_vendas WHERE data_pedido >= :dt) e reinsere só o que
        # este filtro deixa passar. Se as duas janelas divergirem — por exemplo
        # pipeline em 5 meses e transformer em 3 — o loader apaga 5 meses e
        # devolve 3, e DOIS MESES DE VENDA SOMEM SEM AVISO.
        #
        # Agora existe um único ponto de cálculo, no pipeline. O fallback abaixo
        # só cobre chamada isolada em teste.
        # =========================================================================
        if data_inicio_janela is None:
            hoje = date.today()
            data_inicio_janela = (hoje - relativedelta(months=JANELA_MESES)).replace(day=1)
            print(f"⚠️ [SILVER] Janela não recebida do pipeline; assumindo {JANELA_MESES} meses.")
        # Converte para string YYYYMMDD para comparar com a coluna 'dtapedido' da 150
        data_inicio_str = data_inicio_janela.strftime("%Y%m%d")
        print(f"   -> Janela: pedidos a partir de {data_inicio_str}")

        # 1. Tratamento da Tabela de Vendas (150)
        # FILTRO DE OPERACAO — reintroduzido em 2026-09 apos confirmacao contra
        # o ERP: operacao "51" e' bonificacao/transferencia interna, nao venda
        # real. Removido em 2026-08 (base unificada sem exclusao de canal), mas
        # isso inflou o valor de vl_pedido em ~R$2,2 milhoes no periodo jan-ago
        # 2026 (R$163.426.642 apurado vs R$161.216.226 do ERP), distorcendo Fill
        # Rate, WMAPE, BIAS e PMR/PMP, que dependem de vl_pedido/qt_pedido.
        colunas_150 = lf_150.columns
        if "operacao" in colunas_150:
            lf_150 = lf_150.filter(
                pl.col("operacao").cast(pl.Utf8).str.strip_chars() != "51"
            )
        else:
            print("⚠️ [SILVER] Coluna 'operacao' ausente na 150 — filtro de bonificação/transferência não aplicado.")

        # FILTRO DE REGIONAL (EIC / FIFEIRO) — reintroduzido em 2026-09 a pedido
        # explicito do negocio: esses canais NAO podem entrar em NENHUMA metrica,
        # KPI, rateio (distributor.py usa fato_vendas p/ share/PMV) ou base de
        # planejamento (ConsensoArena, dashboards, WMAPE/BIAS/FVA, PMR/PMP).
        # A regional usada aqui e' a propria da 150 (coluna 'regional' de vendas,
        # nao a de dim_clientes/188 — ver comentario mais abaixo sobre o join).
        # Excecao unica: o Painel Financeiro, que NAO le fato_vendas (le de
        # dim_clientes + fontes proprias em app/financeiro/pmr_engine.py) e por
        # isso nao e afetado por este filtro, propositalmente.
        if "regional" in lf_150.columns:
            lf_150 = lf_150.filter(
                ~pl.col("regional").cast(pl.Utf8).str.strip_chars().str.to_uppercase().is_in(["EIC", "FIFEIRO"])
            )
        else:
            print("⚠️ [SILVER] Coluna 'regional' ausente na 150 — filtro de EIC/FIFEIRO não aplicado.")

        lf_vendas = lf_150.filter(
            (pl.col("dtapedido") >= data_inicio_str)   # <-- BLINDAGEM TEMPORAL
        ).with_columns([
            pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("cliente").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("descricao").cast(pl.Utf8).str.strip_chars(),
            pl.col("pedido").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars().fill_null("S/N") 
        ]).with_columns([
            pl.concat_str([pl.col("cliente"), pl.lit("_"), pl.col("loja")]).alias("cliente_loja")
        ])

        # =========================================================================
        # 🛡️ DEDUPLICAÇÃO DE LINHA — defesa contra artefato de paginação.
        #
        # HISTÓRICO: em 2025-11-25 foi confirmada inflação de volume (+31.737 cx)
        # causada por um group_by(pedido, produto, cgc) que juntava clientes
        # DIFERENTES sob cgc="SEM_CGC" (cliente sem cadastro na 188). A correção
        # removeu o group_by inteiro, na premissa de que "cada linha da API 150
        # já é única por (pedido, produto, cliente, loja)" — mas essa premissa
        # nunca foi IMPOSTA em código, só presumida.
        #
        # BUG NOVO CONFIRMADO (2026-08, SKU 410313699, nov/2025): a extração
        # pagina dia a dia por offset (_fetch_dia_paginado em extractor.py). A
        # única defesa contra página repetida é comparar o primeiro registro da
        # página atual com o da anterior — isso só pega REPETIÇÃO TOTAL da
        # página. Não pega SOBREPOSIÇÃO PARCIAL: se a ordenação da API não tem
        # desempate determinístico (ou se registros são inseridos/alterados na
        # janela entre duas chamadas), a MESMA linha pode aparecer em duas
        # páginas adjacentes sem que os primeiros registros de cada uma
        # coincidam. Sem group_by e sem deduplicação, essa linha duplicada
        # atravessa direto para fato_vendas.
        #
        # Confirmado contra a fonte (planilha Pendência Funct, nov/2025):
        # 410313699 soma 31 cx em 11 pedidos distintos na fonte; o dossiê
        # mostrava 36 — a diferença bate com uma única linha duplicada.
        #
        # A chave de unicidade é a mesma que o comentário histórico já cita:
        # (pedido, produto, cliente, loja). Aplicada ANTES do DE-PARA para
        # não interferir na lógica de consolidação COPA (que depende de ver
        # as linhas origem/destino tal como vieram da API).
        #
        # Mantido lazy de propósito (sem .collect() aqui): o pipeline já
        # loga o total de linhas carregadas mais adiante — forçar duas
        # materializações extras só para contar antes/depois custaria uma
        # passada inteira a mais sobre potencialmente anos de histórico
        # numa Recarga Total, sem necessidade.
        # =========================================================================
        lf_vendas = lf_vendas.unique(
            subset=["pedido", "produto", "cliente", "loja"], keep="first"
        )

        # =========================================================================
        # 🔁 DE-PARA DE SKU — aplicado AQUI, antes de qualquer agregação.
        #
        # Precisa vir antes do group_by(["pedido","produto","cgc"]) lá embaixo:
        # se o mesmo pedido tiver linha do código origem e do destino, o group_by
        # SOMA as duas naturalmente numa só. Aplicado depois, sobrariam duas
        # linhas do mesmo par e a contagem de pedidos dobraria.
        #
        # Usa join em vez de replace/map_dict porque a API desses dois muda entre
        # versões do polars; o join funciona em todas. O coalesce troca o SKU e a
        # descrição de uma vez: sem trocar a descrição, o executar_carga_produtos
        # pegaria descricao.first() do agrupamento e renomearia o SKU base na
        # dim_produtos (ex.: 410014792 viraria "...COPA...").
        # =========================================================================
        df_de_para = self._carregar_de_para()
        if not df_de_para.is_empty():
            lf_vendas = lf_vendas.join(
                df_de_para.lazy(), on="produto", how="left"
            ).with_columns([
                pl.coalesce([pl.col("produto_destino"), pl.col("produto")]).alias("produto"),
                pl.coalesce([pl.col("descricao_destino"), pl.col("descricao")]).alias("descricao"),
            ]).drop(["produto_destino", "descricao_destino"])
            pares = ", ".join(
                f"{o}->{d}" for o, d in zip(
                    df_de_para["produto"].to_list()[:6],
                    df_de_para["produto_destino"].to_list()[:6])
            )
            print(f"🔁 [SILVER] DE-PARA aplicado em {df_de_para.height} SKU(s): {pares}")

        
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
        # regional VEM da 188 e precisa ser selecionada aqui, senao o loader
        # grava "N/A" no fallback (origem do bug de regional na dim_clientes).
        lf_clientes = lf_188.select([
            "cod", "loja", "cgc", "razao social", "regional", "bloqueado", "vendedor_nome", "gerente_nome", "supervisor_nome"
        ]).rename({
            "razao social": "cliente_razaosocial" 
        }).with_columns([
            pl.col("cod").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("loja").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
            pl.col("cgc").cast(pl.Utf8).str.strip_chars(),
            pl.col("cliente_razaosocial").cast(pl.Utf8).str.strip_chars(),
            pl.col("regional").cast(pl.Utf8).fill_null("N/A").str.to_uppercase().str.strip_chars(),
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
        # A 150 (vendas) JA tem 'regional' propria, usada no fluxo de vendas.
        # A 188 (clientes) tambem tem 'regional', mas ela e destinada a DIMENSAO
        # (lf_clientes, retornado abaixo). Para o join de vendas, removemos a
        # regional da copia de clientes e evitamos conflito 'regional_right'.
        # lf_clientes (retornado) preserva a regional da 188 para a dim_clientes.
        lf_clientes_join = lf_clientes.drop("regional")
        lf_vendas = lf_vendas.join(
            lf_clientes_join, left_on="cliente_loja", right_on="cod_loja", how="left"
        ).with_columns([
            pl.col("cgc").fill_null("SEM_CGC"),
            pl.col("cliente_razaosocial").fill_null("SEM_NOME"),
            pl.col("bloqueado").fill_null("ATIVO"),
            pl.col("supervisor_nome").fill_null("SEM SUPERVISOR"),
            pl.col("gerente_nome").fill_null("SEM GERENTE"),
            pl.col("vendedor_nome").fill_null("SEM VENDEDOR")
        ])

        # 4. Enriquecimento de Portfólio (Segmentos.xlsx) — SEM FILTRO.
        # PRINCIPIO: a fato_vendas e o PAI da assertividade e contem TUDO que
        # vendeu (inclusive DESCONTINUADO / ECOMMERCE / EXPORTACAO). O filtro de
        # curva pertence ao PLANEJAMENTO (forecaster/rateio), nunca a carga de
        # vendas — senao o Drop&Replace da janela apaga retroativamente as
        # vendas de produtos recem-descontinuados (reescreve o passado).
        # O Segmentos COMPLETO enriquece (bu/categoria/segmento/curva); venda de
        # SKU fora do Segmentos passa com DESCONHECIDO.
        if not df_seg.is_empty():
            df_portfolio = df_seg.with_columns([
                pl.col("produto").cast(pl.Utf8).str.replace(r"\.0$", "").str.strip_chars(),
                pl.col("2026").cast(pl.Utf8).fill_null("").str.strip_chars().str.to_uppercase()
            ]).unique(subset=["produto"], keep="first")

            lf_silver = lf_vendas.join(
                df_portfolio.lazy().select(["produto", "bu", "categoria", "segmento", "2026"]),
                on="produto",
                how="left"
            )
            
            lf_silver = lf_silver.with_columns([
                pl.col("bu").fill_null("DESCONHECIDO"),
                pl.col("categoria").fill_null("DESCONHECIDO"),
                pl.col("segmento").fill_null("DESCONHECIDO"),
                pl.col("2026").fill_null("SEM_CURVA")
            ])
        else:
            return None, None, None

        lf_silver = lf_silver.with_columns(pl.col("cliente").alias("cod_cliente"))
        lf_final = lf_silver.with_columns(pl.col("2026").alias("curva_2026"))
        
        # --- RENOMEAÇÃO FINAL DAS COLUNAS ---
        # O group_by foi REMOVIDO. Motivo:
        #
        # O único caso que precisava de agregação era o DE-PARA de SKU (418→410):
        # se um pedido tivesse o SKU origem E o destino na mesma nota, o join
        # anterior já colapsa ambos para o mesmo produto destino — e o DE-PARA
        # só existe para ~5 SKUs COPA, não para o portfólio inteiro.
        #
        # O group_by genérico em (pedido, produto, cgc) causava inflação de volume
        # porque clientes sem cadastro na 188 recebem cgc="SEM_CGC": pedidos distintos
        # para clientes diferentes (todos com SEM_CGC) do mesmo SKU eram somados
        # numa única linha, duplicando volumes (confirmado em 2025-11-25: +31.737 cx).
        #
        # Solução: preservar a granularidade original da API 150. Cada linha já é
        # única por (id_pedido, produto, cliente, loja). Apenas renomeamos as colunas.
        lf_final = lf_final.rename({
            "produto":    "sku",
            "qtpedido":   "qt_pedido",
            "vlpedido":   "vl_pedido",
            "dtapedido":  "data_pedido",
            "cod_cliente": "cod_cliente",
            "cliente_razaosocial": "cliente_razaosocial",
        })

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

        # Converter data_pedido de string YYYYMMDD para Date e barrar nulos
        lf_final = lf_final.with_columns(
            pl.col("data_pedido").str.to_date("%Y%m%d", strict=False)
        ).filter(pl.col("data_pedido").is_not_null())

        return lf_final, lf_clientes, df_orc_final