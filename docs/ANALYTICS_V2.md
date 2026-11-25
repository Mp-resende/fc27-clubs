# Analytics 2.0 — Documentação da Implementação

> Data: 2026-11-03 · Versão: 0.2 (`v0.1` = ingestão de amistosos)

Este documento descreve todas as melhorias implementadas no FC27 Clubs Monitor:
**ingestão de liga/playoff + snapshot de temporada EA, métricas derivadas,
visualizações, filtros dinâmicos, H2H, sessões e rankings** — além dos testes e
validações executados.

---

## 1. Visão geral do que mudou

| Área | Antes | Agora |
|---|---|---|
| Ingestão | Só amistosos (`friendlyMatch`) | Liga + amistoso + playoff, dedupe por `matchId` |
| Snapshot EA | — | `clubs/overallStats`, `members/stats`, `members/career/stats` |
| Métricas | Nota/gols/assistências básicas | G+A/90, Power Index, consistência, clutch, participação nos gols, rating ajustado por posição |
| Telemetria | Sem eventos (amistoso não tem) | 10 partidas de liga com 16.367 eventos decodificados |
| Filtros | Campeonato/revisão | Tipo, resultado, margem, adversário, janela de dias, N últimos jogos |
| H2H | — | Página por adversário + snapshot do rival on demand |
| Sessões | — | Agrupamento por dia com nota (grade 0–100) |
| Vínculo jogador ↔ membro EA | — | Normalização de nomes + tabela de aliases manual |
| API | matches/players/review/championships | + 10 endpoints de analytics em `/api/v1/clubs/{id}/...` |

---

## 2. Regras de fonte de dados (importantes)

1. **Placar oficial é a verdade.** `clubs[].goals` da EA manda; o `aggregate.goals`
   não é confiável (caso real: agregado 5x27 num placar 0x3). Na ingestão o campo
   `goals` do time é sobrescrito com o placar oficial; o agregado bruto permanece
   no JSON puro para auditoria.
2. **Revisão de W.O. usa gols de jogadores.** Num 3x0/0x3 real os 3 gols sempre
   aparecem creditados a jogadores; num W.O. nenhum jogador marca. A checagem de
   `player_goals == 0` marca como pendente de revisão (o agregado de time não
   participa da decisão).
3. **Eventos só existem em liga/playoff.** Amistosos da EA chegam sem
   `match_event_aggregate`. Partidas amistosas continuam contando nas estatísticas
   básicas (nota, gols, passes resumidos), mas as métricas avançadas mostram
   `has_events = False` e a UI explica o gap — nada de zeros falsos.
4. **Eventos de time são somados dos jogadores.** O agregado de time da EA vem
   zerado; todo total de equipe é calculado somando as linhas dos jogadores que
   entraram em campo.
5. **EA temporada ≠ amostra local.** `club_overall_stats`/`club_members` são
   dados oficiais de temporada (toda a divisão), a amostra local (`matches`) são
   só as partidas capturadas. Os dois nunca são somados no mesmo número; a UI
   etiqueta cada bloco com `Fonte: EA` ou `Fonte: partidas registradas`.
6. **Códigos de resultado EA validados contra dados reais** (`lastMatchN`):
   `1` = vitória, `2` = derrota, `3` = empate, `-1` = sem jogo.
7. **Códigos de tipo de partida**: `1` = liga, `5` = amistoso (strings
   `leagueMatch`/`friendlyMatch`/`playoffMatch` também aceitas em toda a pilha).
8. **Vínculo membro EA ↔ jogador local** é por nome normalizado
   (maiúscula/acento/pontuação insensível). Um nome ambíguo ou diferente exige
   alias manual (`player_aliases`); nada é vinculado por adivinhação.
9. **Nomes inválidos (`-`, vazio) são descartados no parser** — a EA usa esse
   valor para slots não registrados do elenco e eles nunca podem ser ligados.

---

## 3. Estrutura de dados

### Novas tabelas (migração 0005)

- **`club_overall_stats`** — snapshot de temporada por clube: games/wins/ties/
  losses, gols pró/contra, promoções/rebaixamentos, skill rating, tiers, streaks,
  `division_finishes` (JSON) e `last_results` (JSON com os últimos 10 resultados).
- **`club_members`** — estatísticas da temporada por membro (nota, gols,
  assistências, passes, desarmes, MOTM, cartões, `prevGoals0..10` em JSON).
- **`club_member_careers`** — totais de carreira por membro.
- **`player_aliases`** — vínculo manual `alias normalizado → player_key`.
- Índice `ix_matches_type_played_at` para os filtros por tipo/data.

### Migração 0006

- Coluna `player_match_metrics.passes_failed_cross` (evento 37 da EA).
  Sem ela, toda partida de liga com cruzamento errado abortava a ingestão do
  processamento de eventos.

> As tabelas também são criadas automaticamente via `Base.metadata.create_all`
> no startup; para bancos existentes use `alembic upgrade head` (ou rode o sync,
> que documentou o schema em 0005/0006).

### Coluna derivada

- `matches.duration_seconds` — derivado do maior `seconds_played` da partida
  (tempo efetivamente jogado), preenchido na ingestão.

---

## 4. Módulos

### `app/core/analytics.py` (novo) — o núcleo

Função por função:

- **`MatchFilters.from_query / filtered_matches`** — filtros combináveis de
  tipo (`league|playoff|friendly`), janela em dias, N últimos jogos, resultado
  (V/E/D), margem de gols (1, 2, 3+) e adversário. Usa apenas partidas contáveis
  (`review_status` em válidos/corrigidos, `is_ignored = 0`).
- **`local_summary(rows)`** — V/E/D, pontos, gols, saldo, aproveitamento,
  PPG, forma (strip antiga→recente), streak atual, nº de sessões.
- **`official_summary(overall)`** — resume o snapshot EA com derivados
  (PPG, gols/jogo, forma desde `lastMatch0..9` com nomes dos adversários).
- **`player_aggregates(db, club_id, rows)`** — por jogador: G+A/90, `per90()`
  genérico, precisão de passe, MOTM/90, melhor nota, consistência
  (`100 × (1 − desvio/média)`), **clutch** (média da nota em jogos com placar de
  diferença ≤ 1 − média geral), **participação nos gols** ((G+A)/gols do time),
  posição dominante e **rating ajustado** (nota − média da posição na amostra).
- **`_apply_power_index`** — índice composto 0–100: 40% z(G+A/90) +
  25% z(rating ajustado) + 20% z(MOTM/90) + 15% z(minutos), convertido pela CDF
  normal. `sort_players` ordena por power/G+A90/nota/gols/minutos/nome.
- **`team_profile(...)`** — assinatura do time a partir dos eventos dos
  jogadores: passes/90, verticalidade (% de passes para frente), pressão alta
  (recuperações no terço ofensivo/90), ações defensivas/90, chutes no gol/90,
  radar 0–100 com referências fixas (350 passes/90, 8 recuperações/90 etc.),
  recuperações/perdas por terço do campo e direção dos passes (frente/lado/trás).
- **`pass_direction_players`** — tabela per-player para a barra empilhada.
- **`session_reports`** — agrupa por dia; grade = 100 × (50% resultado +
  30% saldo + 20% nota média), melhor jogador e matador da sessão.
- **`squad_view`** — funde membros EA com a amostra local (por `player_key` ou
  nome) e mantém visíveis os jogadores locais sem membro EA (subs/convidados).
- **`goals_chart`** — gols pró/contra na cronologia + **média móvel de 5**.
- **`dashboard_context`** — bloco único consumido pela página inicial.
- **`h2h_summary`** — filtro por adversário + lista de jogos.
- **`match_analytics`** — comparação dos times numa partida (posse, chutes,
  passes, desarmes, faltas, escanteios, defesas) + métricas de evento por clube.
- **`event_coverage`** — quantos eventos o `EVENT_MAP` decodifica (e top-12
  códigos órfãos) para transparência sobre eventos não mapeados.
- **`METRIC_LABELS`** — rótulos em português para exibição dos eventos.
- Constantes: `MATCH_TYPE_*`, `POSITION_*`, resultado/margem — com os tipos
  numéricos e por extenso aceitos em toda a aplicação.

### `app/core/member_link.py` (novo)

`normalize_name` (NFKD + ASCII + casefold + pontuação para espaço),
`local_name_index` (nome normalizado → player_keys vistos no clube),
`link_members` (auto-para nomes únicos; alias vence) e `link_member` (grava
alias manual e liga a linha de temporada/carreira). Nomes `-`/vazios são
ignorados (`is_usable_name`).

### `app/core/parser.py` (extendido)

- `parse_overall_stats` — normaliza o snapshot do clube, incluindo
  `finishesInDivisionNN → {division, group, count}` e `lastMatchN/lastOpponentN`.
- `parse_member_stats` / `parse_member_career` — listas da temporada/carreira,
  ignorando nomes inválidos e serializando `prevGoals0..10` em JSON.

### `app/worker/ingestor.py`

- `DEFAULT_MATCH_TYPES = (leagueMatch, friendlyMatch, playoffMatch)`;
  `_fetch_unique_matches` busca os três, tolera falha de um endpoint e
  **deduplica por `matchId`**.
- `sync_club_profile` — injeta o snapshot EA com upsert (idempotente) para o
  próprio clube **ou um rival**; roda depois das partidas e uma falha de perfil
  nunca reverte a ingestão de partidas (erro isolado em `IngestResult`).
- Placar oficial sobrescreve `team_stats["goals"]`; `duration_seconds`
  derivado; `IngestResult` agora carrega `profile_synced/members_synced/profile_error`.

### `app/core/review.py`

- W.O. 3x0/0x3 — mistério resolvido: só `player_goals` decide (ver §2.2).
  Resultados corrigidos/ignorados continuam protegidos de reingestão.

### `app/dashboard/routes.py`

Reescritas/novas: `/` (visão geral EA+amostra), `/partidas` (+ filtro tipo),
`/partidas/{id}` (analytics de partida), `/jogadores` (elenco/cards/scatter/
vinculação), `/jogadores/{key}` (perfil completo), **`/analise`** (novo),
**`/h2h`** (novo), `/h2h/{opponent_id}/atualizar` (snapshot do rival on demand),
**`/vincular-membro`** (novo), `/telemetria` (eventos + cobertura) e `/status`.

### `app/api/routers/analytics.py` (novo, registrado em `app/api/main.py`)

Fonte EA em chave separada (`source: "ea"`) para o consumidor nunca confundir:

- `GET  /api/v1/clubs/{id}/season/overall`
- `GET  /api/v1/clubs/{id}/season/members`
- `POST /api/v1/clubs/{id}/season/sync` — snapshot on demand (clube ou rival)
- `GET  /api/v1/clubs/{id}/analytics/summary` — resumo local + oficial + gráfico
- `GET  /api/v1/clubs/{id}/analytics/players?sort=&tipo=&dias=&n=&resultado=&margem=&adversario=`
- `GET  /api/v1/clubs/{id}/analytics/team-profile`
- `GET  /api/v1/clubs/{id}/analytics/sessions`
- `GET  /api/v1/clubs/{id}/analytics/pass-directions`
- `GET  /api/v1/clubs/{id}/h2h/{opponent_id}`
- `GET  /api/v1/clubs/{id}/matches/{match_id}/analytics`

Todos aceitam os filtros combináveis; respostas marcando fonte/limite de
telemetria onde aplicável.

### CLI (`__main__.py`)

- **`python __main__.py sync`** — importa as três fontes de jogo, aplica o
  snapshot EA, reporta contagens/erros e fecha. Tabelas novas garantidas no boot.

### Templates e UI

- `base.html` — nav com Análise/H2H, flashes globais, footer v0.2.
- `index.html` — KPIs EA + amostra lado a lado (seções rotuladas), strips de
  forma, gráfico de gols com média móvel, últimas partidas, destaque top 5 e
  aviso quando a amostra não tem telemetria.
- `players.html` — cartões do elenco (overall EA + temporada + métricas locais),
  filtros por posição/ordem, scatter gols×assist por 90 com cor por posição e
  formulário de vínculo membros↔jogadores.
- `player_detail.html` — métricas do jogador (G+A/90, power, consistência,
  clutch, participação), blocos temporada/carreira EA, gráfico de evolução com
  gráfico de linha, tabela de partidas com placar/tipo/nota/MOM.
- `analytics.html` (novo) — performance da amostra, gráfico de gols com média
  móvel, assinatura do time (radar + indicadores), terços do campo, direção de
  passes por jogador, grade de sessões com nota e tabela de ranking 2.0.
- `h2h.html` (novo) — bloco versus, totais, jogos e modo de busca de snapshot do
  rival.
- `telemetry.html` — KPIs de eventos, cobertura de eventos mapeados, radar,
  barras de direção, terços, tabela por jogador, top-12 códigos órfãos e nota
  de fonte limitada.
- `match_detail.html` — painel de comparação de times + explicação de
  ausência/presença de eventos.
- `matches.html` — filtro por tipo de partida + coluna de tipo na tabela.

### Estilo (`app/dashboard/static/style.css`)

Blocos novos: `.filter-form` (seletores temáticos), `.source-tag.ea/.local`,
strips `.form-pill w/d/l/goal`, badges de tipo/resultado, `.player-card*`
(overall, data dl de três colunas, cartão hachurado para sem vínculo),
`.pos-badge gk/def/mid/fwd`, `.chart-grid` (radar/barra lado a lado), grades e
cards de sessão, e bloco versus `.h2h-vs`.

---

## 5. Como usar

```bash
# 1) Sincronizar tudo de uma vez (partidas liga+amistoso+playoff + snapshot EA)
python __main__.py sync

# 2) Servidor com dashboard e API
python __main__.py api         # http://127.0.0.1:8000
python __main__.py both        # worker em background + servidor

# 3) Migrações (bancos já existentes)
.venv\Scripts\python.exe -m alembic upgrade head

# 4) Testes
.venv\Scripts\python.exe -m pytest
```

Atalhos do dashboard: `/` visão geral · `/analise` análise do time ·
`/h2h` confrontos · `/jogadores` elenco · `/telemetria` eventos ·
`/status` runs de ingestão.

Na página de confronto, se o rival ainda não tem snapshot EA, aparece o botão
“Buscar dados do adversário na EA” (chamada direta às APIs de temporada dele).

Para corrigir vínculos membros↔jogadores, abra `/jogadores`, expanda
“Vincular a jogador local” no cartão do membro sem link e escolha o jogador.
A associação fica salva em `player_aliases` e sobrevive a futuras ingestões.

---

## 6. Métricas derivadas — definição

| Métrica | Fórmula |
|---|---|
| G+A/90 | `(gols + assist) × 90 × 60 / segundos` |
| Power Index | CFD normal de `0.40·z(G+A/90) + 0.25·z(rating−média da posição) + 0.20·z(MOTM/90) + 0.15·z(min)` (0–100) |
| Consistência | `100 × (1 − σ notas / média)` (quanto mais alta, mais estável) |
| Clutch | média em jogos com diferença de placar ≤ 1 − média geral |
| Participação nos gols | `(G+A do jogador) / gols do time × 100` |
| Rating ajustado | nota − média de nota dos jogadores da mesma posição |
| Verticalidade | passes para frente / (frente+lado+trás) |
| Pressão alta | recuperações no terço ofensivo / 90 |
| Sólido defensivo | (desarme ganho + interceptação) / 90 |

Referências do radar (valores “100”) podem ser recalibradas — estão
centralizadas em `team_profile` no `analytics.py`.

---

## 7. Validação executada (banco real, 03/11/2026)

- ✅ `pytest`: **82 testes passando**, incluindo ingestão idempotente multi-tipo,
  revisão de W.O., parsers de temporada, vínculo nome↔player_key e analytics.
- ✅ `python __main__.py sync` no DB real: **20 partidas** retornadas (10 de liga
  novas com eventos + 10 amistosos já conhecidos — dedupe idempotente: 12
  processadas como novas/atualizadas, sem duplicatas), perfil EA atualizado
  com 23 membros + 23 carreiras e **17 links automáticos** para os jogadores
  locais (12 restantes sem vínculo — nomes `-`/ambíguos, resolvíveis via alias).
- ✅ Teleamento viva: **10 partidas de liga com eventos** (16.367 eventos) e
  364 linhas de métricas per-player decodificadas; amistosos seguem sem eventos
  (comportamento esperado da EA).
- ✅ Endpoint checks: `/`, `/partidas`, `/jogadores`, `/analise`, `/h2h`,
  `/telemetria`, `/revisao`, `/campeonatos`, `/status`, `/partidas/{id}`,
  `/jogadores/{id}` e os 10 endpoints `/api/v1/...` — **todos 200**.
- ✅ Backup pré-sync: `data/backups/manual_before_sync_20261103.db`.

Correções feitas durante a validação:

1. Coluna faltante `passes_failed_cross` (evento 37) abortava ingestão de liga —
   adicionada + migração 0006.
2. `_club_name_map` da API usava PK composta indevidamente (`session.get(Club, id)`)
   — corrigido para lookup por `{club_id, platform}` em todos os endpoints.

---

## 8. Limitações conhecidas e próximos passos

- **playoffMatch** retorna vazio para o clube (a divisão atual não tem jogos de
  playoff registrados) — o código está pronto para receber quando houver.
- **65 códigos de evento órfãos** no decodificador (`EVENT_MAP` cobre 78 de 143
  códigos distintos). Os mais frequentes (`8, 114, 121, 151, 157, 178, 184, 119,
  150, 142`) podem ser mapeados ao verificar o anche com o EVENT_MAP — a página
  de telemetria mostra os top-12 para guiar o mapeamento.
- 12 membros ficam sem vínculo automático — usar o formulário de alias.
- Playoffs entre clubes em semanas futuras vão aparecer em `MATCH_TYPE_FILTERS`
  automaticamente pela key `playoff`.

---

## 9. Arquivos tocados

```
app/
  api/routers/analytics.py            (novo)
  api/main.py                         (router registrado)
  core/analytics.py                   (novo, ~900 linhas)
  core/member_link.py                 (novo)
  core/parser.py                      (+ parsers de temporada, guard de nomes)
  core/review.py                      (W.O. agora por player_goals)
  core/championships.py               (+ label de tipo em match_row)
  db/models.py                        (+4 tabelas +1 coluna)
  dashboard/routes.py                 (rotas reescritas/novas)
  dashboard/templates/*.html          (7 reescritos, 2 novos)
  dashboard/static/style.css          (bloco Analytics 2.0)
  worker/ingestor.py                  (multi-tipo, dedupe, sync_club_profile)
migrations/versions/
  0005_season_snapshot.py             (novo)
  0006_add_passes_failed_cross.py     (novo)
tests/
  test_analytics.py                   (novo)
  test_profiles.py                    (novo)
  test_ingest_profile.py              (novo)
__main__.py                           (comando `sync`)
docs/ANALYTICS_V2.md                  (este arquivo)
```
