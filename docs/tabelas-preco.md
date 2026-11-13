# Tabelas de preço do Órulo → categoria social (HIS1, HIS2, HMP, R2V, NR)

Pedido do Moisés em 12/nov/2026: guardar em cada unidade a categoria que a
tabela de preços (PDF do Órulo) informa.

## Onde fica

- `im_unidades.categoria_social`: `his1`, `his2`, `hmp`, `r2v`, `nr` ou
  `nao_informado`. Nulo = ainda não processada (por exemplo, unidade que entrou
  depois que a tabela do empreendimento foi lida).
- `im_unidades.categoria_social_fonte`: `tabela_unidade` (a linha da unidade
  traz a categoria), `tabela_grupo` (regra por tipo, "Finais 01, 02 – 1º ao 19º
  andar – HIS1", expandida), `nota_geral` ("todas as unidades são HIS2"),
  `manual` (nunca sobrescrita) ou, para `nao_informado`, o motivo:
  `fora_da_tabela`, `tabela_sem_categoria`, `sem_tabela`.
- `im_tabelas_preco`: uma linha por tabela lida (arquivo do Órulo, link, mês,
  status, método de texto, contagens) ou uma linha sem arquivo quando o
  empreendimento não tem tabela.

Migration: `supabase/migrations/20261113010000_categoria_social.sql`.

## Como roda

- Na `coleta-mensal`, depois do fechamento e do agrupamento:
  `etapa_tabelas` pega `im_tabelas_pendentes` (empreendimentos sem tabela
  processada, ou seja, os novos; `sem_tabela` de lançamento/breve lançamento é
  tentado de novo depois de 25 dias; `erro` é tentado de novo) e, um por vez:
  detalhe logado → arquivos `Tabela` (sem `_disponibilidade`) → link do PDF →
  download → texto (camada de texto do PDF, senão OCR com RapidOCR) →
  casamento com as unidades do banco → `im_registrar_tabela_preco`.
  `COLETAR_TABELAS=0` desliga. Falha aqui não muda o resultado da coleta.
- Carga avulsa (feita na box em 12–13/nov/2026 para São Paulo):
  `python -m coletar.tabelas --municipio "São Paulo" --workers 3 --pasta pdfs`.
  Retomável: quem já tem linha em `im_tabelas_preco` é pulado.
- Mesmo cuidado do coletor: um pedido por vez ao Órulo com espera
  `ORULO_ATRASO_MIN/MAX`; 403/429 ou login recusado param a etapa, sem tentar
  outro caminho.

## Limites conhecidos

- Muitas tabelas listam só as unidades disponíveis: as vendidas ficam
  `nao_informado` / `fora_da_tabela`.
- Tabela que marca a categoria só por cor (ex.: Well Perdizes) ou por "HIS"
  sem número fica sem categoria por unidade.
- OCR erra às vezes; a unidade só recebe categoria quando o número casa com uma
  unidade do empreendimento e, havendo mais de uma torre, a torre também.
