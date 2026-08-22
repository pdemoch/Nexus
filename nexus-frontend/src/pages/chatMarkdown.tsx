// chatMarkdown.tsx — renderizador de markdown leve para bolhas de chat.
//
// O agente responde em markdown leve: ## títulos, **negrito**, tabelas
// | pipe |, e uma linha final "Leitura: ...". Sem isso, tudo vira texto
// cru com pipes e cerquilhas visíveis — era o bug original do chat de
// KPIs que motivou este parser, e agora é compartilhado pelo Assistente
// único da Sidebar (que herda os dois modos, Indicadores e Demanda).
//
// Regra que os PROMPTS dos agentes seguem e este parser espelha:
//   título:  "## " ou "### " (dois ou três #, nunca um # sozinho)
//   negrito: **texto**
//   tabela:  linha com | seguida de linha separadora ---|---
//   conclusão: linha iniciada por "Leitura: "

function renderInlineBold(texto: string, keyBase: string) {
  const partes = texto.split(/(\*\*[^*]+\*\*)/g);
  return partes.map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <strong key={`${keyBase}-b${i}`}>{p.slice(2, -2)}</strong>
      : <span key={`${keyBase}-t${i}`}>{p}</span>
  );
}

export function renderChatMarkdown(conteudo: string) {
  const linhas = (conteudo || '').split('\n');
  const blocos: any[] = [];
  let i = 0, k = 0;

  const ehSeparadorTabela = (l: string) => /^\s*\|?[\s:-]+\|[\s:|-]*\|?\s*$/.test(l || '');
  const partirCelulas = (l: string) =>
    l.split('|').map(c => c.trim()).filter((c, idx, arr) => !(idx === 0 && c === '') && !(idx === arr.length - 1 && c === ''));

  while (i < linhas.length) {
    const linha = linhas[i];

    if (linha.trim() === '') { i++; continue; }

    // Tabela: linha com | seguida de linha separadora ---|---
    if (linha.trim().startsWith('|') && ehSeparadorTabela(linhas[i + 1])) {
      const cabecalho = partirCelulas(linha);
      let j = i + 2;
      const corpo: string[][] = [];
      while (j < linhas.length && linhas[j].trim().startsWith('|')) {
        corpo.push(partirCelulas(linhas[j]));
        j++;
      }
      blocos.push(
        <div key={`tbl-${k++}`} style={{ overflowX: 'auto', margin: '8px 0', border: '1px solid #f1f5f9', borderRadius: 8 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 11.5 }}>
            <thead>
              <tr>
                {cabecalho.map((h, hi) => (
                  <th key={hi} style={{
                    textAlign: hi === 0 ? 'left' : 'right', padding: '7px 12px',
                    background: '#eef2ff', color: '#3730a3', fontWeight: 800, fontSize: 10.5,
                    letterSpacing: '.02em', borderBottom: '2px solid #c7d2fe', whiteSpace: 'nowrap',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {corpo.map((r, ri) => (
                <tr key={ri} style={{ background: ri % 2 ? '#f8fafc' : '#fff' }}>
                  {r.map((c, ci) => (
                    <td key={ci} style={{
                      padding: '7px 12px', textAlign: ci === 0 ? 'left' : 'right',
                      borderBottom: '1px solid #f1f5f9', color: '#334155', whiteSpace: 'nowrap',
                    }}>{renderInlineBold(c, `td-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      i = j;
      continue;
    }

    // Título ## ou ### (um # sozinho NÃO conta — cai como parágrafo)
    const mHeader = linha.match(/^(#{2,3})\s+(.*)$/);
    if (mHeader) {
      blocos.push(
        <div key={`h-${k++}`} style={{
          fontSize: mHeader[1].length === 2 ? 12.5 : 12, fontWeight: 900, color: '#0f172a',
          marginTop: blocos.length ? 12 : 0, marginBottom: 4,
        }}>{mHeader[2]}</div>
      );
      i++;
      continue;
    }

    // Linha de conclusão "Leitura: ..."
    if (/^Leitura:\s*/i.test(linha.trim())) {
      blocos.push(
        <div key={`read-${k++}`} style={{
          marginTop: 8, padding: '9px 13px', background: '#fffbeb', border: '1px solid #fde68a',
          borderRadius: 8, fontSize: 12, color: '#92400e', fontWeight: 600, lineHeight: 1.5,
        }}>{renderInlineBold(linha.trim(), `read-${k}`)}</div>
      );
      i++;
      continue;
    }

    // Parágrafo: acumula até linha em branco, tabela, título ou "Leitura:"
    let j = i;
    const paraLinhas: string[] = [];
    while (j < linhas.length && linhas[j].trim() !== ''
           && !linhas[j].trim().startsWith('|')
           && !/^#{2,3}\s/.test(linhas[j])
           && !/^Leitura:\s*/i.test(linhas[j].trim())) {
      paraLinhas.push(linhas[j]);
      j++;
    }
    blocos.push(
      <p key={`p-${k++}`} style={{ margin: '0 0 8px 0' }}>
        {renderInlineBold(paraLinhas.join(' '), `p-${k}`)}
      </p>
    );
    i = j;
  }

  return blocos;
}