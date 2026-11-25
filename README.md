---
title: Upzin Clubs Suite
emoji: ⚽
colorFrom: green
colorTo: blue
sdk: docker
app_port: 8000
pinned: false
---

# FC27 Clubs Monitor & Upzin Suite

Aplicação Python que coleta partidas amistosas do EA FC 27 (modo Clubs) via API não oficial, persiste em SQLite e expõe via FastAPI + dashboard Jinja2/HTMX.

## Requisitos

- Python 3.12+
- pip ou uv

## Instalação

```bash
# Clone e entre na pasta
cd fc27-clubs

# Crie o virtualenv
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS

# Instale as dependências
pip install -e ".[dev]"
```

## Configuração

Copie `.env.example` para `.env` e ajuste se necessário:

```bash
copy .env.example .env
```

Os valores padrão já apontam para o clube **MVL ES** (ID `682077`) na plataforma `common-gen5`.

## Como iniciar

### Apenas o servidor web (API + dashboard)

```bash
python __main__.py api
```

### Apenas o worker de ingestão

```bash
python __main__.py worker
```

### Os dois juntos (recomendado para uso local)

```bash
python __main__.py both
```

O worker faz polling a cada ~5 minutos (±30 s de jitter) e salva as partidas (liga, amistoso e playoff, deduplicadas por `matchId`) em `data/fc27_clubs.db`, além de manter o **snapshot de temporada EA** (overallStats, membros, carreiras).

### Ciclo único de sincronização

```bash
python __main__.py sync
```

A análise do time (métricas derivadas, H2H, sessões, telemetria) está documentada em [`docs/ANALYTICS_V2.md`](docs/ANALYTICS_V2.md).

> **Importante:** execute os comandos acima dentro da pasta `fc27-clubs/`. O pacote importável
> chama-se `app`; **não existe** o módulo `fc27_clubs`. Por isso `python -m fc27_clubs ...`
> falha com `No module named fc27_clubs`. Use `python __main__.py ...` (ou `python . both`).

## Dashboard

Acesse [http://127.0.0.1:8000](http://127.0.0.1:8000) no navegador após iniciar o servidor.

| Página | URL |
|---|---|
| Visão geral | `/` |
| Lista de partidas | `/partidas` |
| Súmula de partida | `/partidas/{match_id}` |
| Revisão de resultados | `/revisao` |
| Campeonatos privados | `/campeonatos` |
| Campanha do campeonato | `/campeonatos/{id}` |
| Elenco / ranking | `/jogadores` |
| Perfil do jogador | `/jogadores/{player_key}` |
| Telemetria | `/telemetria` |
| Status do sistema | `/status` |

## Revisão de resultados

O endpoint de amistosos da EA não é confiável em dois casos:

1. **Partida abandonada pelos dois clubes** — os times iniciam e saem para
   jogar outra; a EA lista a partida com duração mínima e sem estatísticas.
   O resultado é **ignorado** automaticamente.
2. **Desistência em fase de grupos** — a EA grava o placar padrão `3x0`/`0x3`
   mesmo que o jogo estivesse empatado. A partida vai para revisão.

Cada partida tem um `review_status`:

| Status | Significado |
|---|---|
| `valid` | Resultado da EA aceito e contabilizado |
| `needs_review` | Suspeito; fica **fora** das estatísticas até confirmação |
| `ignored` | Resultado descartado (amistoso abandonado ou decisão manual) |
| `corrected` | Placar substituído pelo resultado real (manual ou print) |

O placar original da EA é preservado em `ea_club_a_goals`/`ea_club_b_goals`
para auditoria. Um novo ingest nunca sobrescreve partidas `ignored` ou
`corrected`.

A página `/revisao` lista a fila e permite confirmar, ignorar ou corrigir o
placar. Na súmula (`/partidas/{match_id}`) também é possível enviar um **print
do resumo da partida**: o sistema tenta extrair o placar via API multimodal
(`VISION_*`), depois via OCR local (`pytesseract`) e, se nada for reconhecido,
o placar pode ser digitado manualmente. Nada é aplicado sem confirmação, exceto
quando o upload é feito com `apply=true`.

Para habilitar OCR local:

```bash
pip install -e ".[dev,ocr]"
```

Para usar a extração por visão, configure `VISION_API_KEY` (e opcionalmente
`VISION_API_URL`/`VISION_MODEL`) no `.env`.

## API REST

Documentação interativa disponível em [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Exemplos rápidos:

```bash
# Saúde
curl http://127.0.0.1:8000/health

# Ficha do clube
curl http://127.0.0.1:8000/api/v1/clubs/682077

# Últimas partidas
curl http://127.0.0.1:8000/api/v1/clubs/682077/matches

# Súmula completa
curl http://127.0.0.1:8000/api/v1/matches/{match_id}

# Elenco com estatísticas
curl http://127.0.0.1:8000/api/v1/clubs/682077/players

# Forçar ingestão imediata
curl -X POST http://127.0.0.1:8000/api/v1/clubs/682077/sync

# Histórico de ingestões
curl http://127.0.0.1:8000/api/v1/ingest/runs

# Campeonatos privados
curl http://127.0.0.1:8000/api/v1/championships

# Classificar uma partida em um campeonato
curl -X POST http://127.0.0.1:8000/api/v1/matches/{match_id}/classification \
  -H "Content-Type: application/json" \
  -d '{"championship_id":1,"stage":"Fase de grupos","round":"1"}'

# Fila de revisão / reanálise automática
curl http://127.0.0.1:8000/api/v1/review/queue
curl -X POST http://127.0.0.1:8000/api/v1/review/scan

# Corrigir, ignorar, confirmar ou restaurar um resultado
curl -X POST http://127.0.0.1:8000/api/v1/matches/{match_id}/review/correct \
  -H "Content-Type: application/json" \
  -d '{"club_a_goals":1,"club_b_goals":1,"note":"Empate real"}'
curl -X POST http://127.0.0.1:8000/api/v1/matches/{match_id}/review/ignore
curl -X POST http://127.0.0.1:8000/api/v1/matches/{match_id}/review/confirm
curl -X POST http://127.0.0.1:8000/api/v1/matches/{match_id}/review/restore

# Enviar print do resumo da partida para extração do placar
curl -X POST http://127.0.0.1:8000/api/v1/matches/{match_id}/review/evidence \
  -F "file=@sumula.png" -F "apply=false"
```

## Testes

```bash
pytest -v
```

## Migrations (Alembic)

```bash
# Aplicar schema inicial
alembic upgrade head

# Gerar nova migration após alterar models
alembic revision --autogenerate -m "descricao"
```

## Aviso sobre os Termos de Serviço da EA

Esta aplicação utiliza a API não oficial `proclubs.ea.com/api/fc`, que **não é suportada ou endossada pela Electronic Arts**. O uso desta API pode violar os Termos de Serviço da EA. Utilize por conta e risco próprios, exclusivamente para fins pessoais e não comerciais.

## Atribuição

Mapeamento de eventos derivado do projeto comunitário [`Interactive-63/eafc-pro-clubs-api-research`](https://github.com/Interactive-63/eafc-pro-clubs-api-research).
