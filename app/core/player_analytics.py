"""Motor de Telemetria, Analytics e Scouting Individual de Jogadores (estilo SpotXI).

Fornece cálculo de perfil consolidado de atletas, médias ponderadas,
moda de posição principal, score de desempenho (0 a 100) e motor de busca/suggest.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.review import COUNTED_STATUSES
from app.db.models import Club, Match, Player, PlayerMatchStats, PlayerName


def calculate_performance_score(
    position: str,
    stats: dict[str, Any],
    matches: int,
    opponent_avg_score: float = 50.0,
) -> float:
    """Calcula pontuação de Desempenho de 0 a 100 ponderada por linha e oponente."""
    if matches <= 0:
        return 0.0

    # Penalidade bayesiana para atletas com menos de 20 partidas
    min_qualified = 20
    confidence = min(1.0, matches / min_qualified) if matches < min_qualified else 1.0

    goals = stats.get("goals", 0)
    assists = stats.get("assists", 0)
    tackles = stats.get("tackles_made", 0)
    saves = stats.get("saves", 0)
    rating = stats.get("avg_rating", 6.0) or 6.0

    per_match_goals = goals / max(matches, 1)
    per_match_assists = assists / max(matches, 1)
    per_match_tackles = tackles / max(matches, 1)
    per_match_saves = saves / max(matches, 1)

    pos = (position or "mid").lower()

    if pos in ["st", "cf", "lw", "rw", "forward"]:
        raw = (per_match_goals * 35.0) + (per_match_assists * 18.0) + ((rating - 6.0) * 15.0)
    elif pos in ["cam", "cm", "cdm", "lm", "rm", "mid", "midfielder"]:
        raw = (per_match_assists * 30.0) + (per_match_goals * 15.0) + (per_match_tackles * 8.0) + ((rating - 6.0) * 15.0)
    elif pos in ["cb", "lb", "rb", "lwb", "rwb", "def", "defender"]:
        raw = (per_match_tackles * 25.0) + (per_match_assists * 8.0) + ((rating - 6.0) * 20.0)
    elif pos in ["gk", "goalkeeper"]:
        raw = (per_match_saves * 18.0) + ((rating - 5.5) * 25.0)
    else:
        raw = ((rating - 6.0) * 20.0) + (per_match_goals * 10.0)

    # Fator de dificuldade do oponente
    opponent_factor = 0.8 + (opponent_avg_score / 250.0)
    adjusted = raw * opponent_factor

    # Normalização em curva sigmoide para escala [0.0, 100.0]
    score = 100.0 / (1.0 + math.exp(-0.06 * (adjusted - 42.0)))
    return round(score * confidence, 2)


def get_player_full_profile(db: Session, player_key: str) -> dict[str, Any] | None:
    """Consolida toda a telemetria, histórico de nomes, clubes e forma recente de um atleta."""
    player = db.get(Player, player_key)
    if player is None:
        return None

    # 1. Histórico de Nomes do Jogador
    name_rows = (
        db.query(PlayerName)
        .filter(PlayerName.player_key == player_key)
        .order_by(PlayerName.last_seen_at.desc())
        .all()
    )
    current_name = name_rows[0].playername if name_rows else player_key
    aliases = [r.playername for r in name_rows if r.playername != current_name]

    # 2. Todas as partidas válidas do atleta (em qualquer clube)
    rows = (
        db.query(
            PlayerMatchStats,
            Match.played_at,
            Match.duration_seconds,
            Club.name.label("club_name"),
        )
        .join(Match, Match.match_id == PlayerMatchStats.match_id)
        .outerjoin(Club, Club.club_id == PlayerMatchStats.club_id)
        .filter(
            PlayerMatchStats.player_key == player_key,
            Match.review_status.in_(COUNTED_STATUSES),
        )
        .order_by(Match.played_at.desc())
        .all()
    )

    if not rows:
        return {
            "player_key": player_key,
            "current_name": current_name,
            "aliases": aliases,
            "platform": player.platform,
            "total_matches": 0,
            "message": "Nenhuma partida válida registrada para este atleta.",
        }

    total_matches = len(rows)

    # 3. Posição Principal (Moda Estatística)
    positions = [pms.position for pms, _, _, _ in rows if pms.position]
    position_counter = Counter(positions)
    primary_position = position_counter.most_common(1)[0][0] if position_counter else "MID"

    # 4. Agregações de Telemetria
    total_goals = sum(pms.goals or 0 for pms, _, _, _ in rows)
    total_assists = sum(pms.assists or 0 for pms, _, _, _ in rows)
    total_shots = sum(pms.shots or 0 for pms, _, _, _ in rows)
    total_passes_made = sum(pms.passes_made or 0 for pms, _, _, _ in rows)
    total_pass_attempts = sum(pms.pass_attempts or 0 for pms, _, _, _ in rows)
    total_tackles_made = sum(pms.tackles_made or 0 for pms, _, _, _ in rows)
    total_tackle_attempts = sum(pms.tackle_attempts or 0 for pms, _, _, _ in rows)
    total_saves = sum(pms.saves or 0 for pms, _, _, _ in rows)
    total_goals_conceded = sum(pms.goals_conceded or 0 for pms, _, _, _ in rows)
    total_clean_sheets = sum(
        (pms.clean_sheets_any or pms.clean_sheets_def or pms.clean_sheets_gk or 0)
        for pms, _, _, _ in rows
    )
    total_moms = sum(pms.is_mom or 0 for pms, _, _, _ in rows)
    total_red_cards = sum(pms.red_cards or 0 for pms, _, _, _ in rows)
    total_seconds = sum(pms.seconds_played or 0 for pms, _, _, _ in rows)

    ratings = [pms.rating for pms, _, _, _ in rows if pms.rating is not None]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else 0.0

    pass_accuracy_pct = (
        round((total_passes_made / total_pass_attempts) * 100.0, 1)
        if total_pass_attempts > 0
        else 0.0
    )
    tackle_success_pct = (
        round((total_tackles_made / total_tackle_attempts) * 100.0, 1)
        if total_tackle_attempts > 0
        else 0.0
    )

    # 5. Clubes defendidos
    clubs_seen: dict[str, dict[str, Any]] = {}
    for pms, _, _, club_name in rows:
        cid = pms.club_id
        if cid not in clubs_seen:
            clubs_seen[cid] = {"club_id": cid, "name": club_name or cid, "matches": 0}
        clubs_seen[cid]["matches"] += 1
    clubs_list = sorted(clubs_seen.values(), key=lambda c: c["matches"], reverse=True)

    # 6. Score de Desempenho (0 a 100)
    stats_dict = {
        "goals": total_goals,
        "assists": total_assists,
        "tackles_made": total_tackles_made,
        "saves": total_saves,
        "avg_rating": avg_rating,
    }
    performance_score = calculate_performance_score(primary_position, stats_dict, total_matches)

    # 7. Forma Recente (Últimos 15 jogos)
    recent_form = []
    for pms, played_at, _, club_name in rows[:15]:
        recent_form.append({
            "match_id": pms.match_id,
            "played_at": played_at,
            "club_id": pms.club_id,
            "club_name": club_name or pms.club_id,
            "position": pms.position,
            "rating": pms.rating,
            "goals": pms.goals or 0,
            "assists": pms.assists or 0,
            "tackles": pms.tackles_made or 0,
            "saves": pms.saves or 0,
            "is_mom": bool(pms.is_mom),
        })

    return {
        "player_key": player_key,
        "ea_player_id": player.ea_player_id,
        "current_name": current_name,
        "aliases": aliases,
        "platform": player.platform,
        "primary_position": primary_position,
        "performance_score": performance_score,
        "summary": {
            "total_matches": total_matches,
            "avg_rating": avg_rating,
            "total_goals": total_goals,
            "goals_per_match": round(total_goals / total_matches, 2),
            "total_assists": total_assists,
            "assists_per_match": round(total_assists / total_matches, 2),
            "man_of_the_match": total_moms,
            "total_seconds_played": total_seconds,
            "avg_minutes_per_match": round((total_seconds / total_matches) / 60, 1),
        },
        "telemetry": {
            "passing": {
                "passes_made": total_passes_made,
                "pass_attempts": total_pass_attempts,
                "pass_accuracy_pct": pass_accuracy_pct,
                "passes_per_match": round(total_passes_made / total_matches, 1),
            },
            "defending": {
                "tackles_made": total_tackles_made,
                "tackle_attempts": total_tackle_attempts,
                "tackle_success_pct": tackle_success_pct,
                "tackles_per_match": round(total_tackles_made / total_matches, 1),
                "clean_sheets": total_clean_sheets,
            },
            "shooting": {
                "shots": total_shots,
                "goals": total_goals,
                "conversion_rate_pct": round((total_goals / total_shots) * 100.0, 1) if total_shots > 0 else 0.0,
            },
            "goalkeeping": {
                "saves": total_saves,
                "goals_conceded": total_goals_conceded,
                "saves_per_match": round(total_saves / total_matches, 1),
            },
            "discipline": {
                "red_cards": total_red_cards,
            },
        },
        "clubs": clubs_list,
        "recent_form": recent_form,
    }


def search_suggest_entities(
    db: Session,
    query_text: str,
    search_type: str = "all",
    limit: int = 15,
) -> list[dict[str, Any]]:
    """Autocomplete global de busca estilo SpotXI /api/stats/suggest."""
    q_clean = query_text.strip()
    if len(q_clean) < 2:
        return []

    pattern = f"%{q_clean}%"
    results: list[dict[str, Any]] = []

    # 1. Busca de Jogadores (por gamertag atual, nomes anteriores ou EA ID)
    if search_type in ["all", "players"]:
        player_matches = (
            db.query(
                Player.player_key,
                Player.platform,
                Player.ea_player_id,
                PlayerName.playername,
                func.count(PlayerMatchStats.match_id).label("matches_count"),
                func.sum(PlayerMatchStats.goals).label("total_goals"),
                func.avg(PlayerMatchStats.rating).label("avg_rating"),
            )
            .join(PlayerName, PlayerName.player_key == Player.player_key)
            .outerjoin(PlayerMatchStats, PlayerMatchStats.player_key == Player.player_key)
            .filter(
                or_(
                    PlayerName.playername.ilike(pattern),
                    Player.player_key.ilike(pattern),
                    Player.ea_player_id.ilike(pattern),
                )
            )
            .group_by(Player.player_key, PlayerName.playername)
            .order_by(func.count(PlayerMatchStats.match_id).desc())
            .limit(limit)
            .all()
        )

        for p in player_matches:
            results.append({
                "kind": "player",
                "id": p.player_key,
                "label": p.playername,
                "platform": p.platform,
                "ea_player_id": p.ea_player_id,
                "meta": f"{p.platform} · {p.matches_count} jogos",
                "matches": p.matches_count or 0,
                "goals": p.total_goals or 0,
                "avg_rating": round(p.avg_rating, 2) if p.avg_rating else None,
                "href": f"/api/v1/players/{p.player_key}/profile",
            })

    # 2. Busca de Clubes
    if search_type in ["all", "clubs"]:
        club_matches = (
            db.query(
                Club.club_id,
                Club.name,
                Club.platform,
                Club.crest_url,
            )
            .filter(
                or_(
                    Club.name.ilike(pattern),
                    Club.club_id.ilike(pattern),
                )
            )
            .limit(limit)
            .all()
        )

        for c in club_matches:
            results.append({
                "kind": "club",
                "id": c.club_id,
                "label": c.name,
                "platform": c.platform,
                "meta": f"Clube · {c.platform}",
                "crest_url": c.crest_url,
                "href": f"/api/v1/clubs/{c.club_id}",
            })

    return results[:limit]


def compare_players_profiles(db: Session, player_keys: list[str]) -> dict[str, Any]:
    """Compara 2 ou mais atletas lado a lado com deltas (+/-) estilo SpotXI /comparar/."""
    profiles: list[dict[str, Any]] = []
    for key in player_keys:
        p = get_player_full_profile(db, key.strip())
        if p and "summary" in p:
            profiles.append(p)

    if len(profiles) < 2:
        return {
            "error": "São necessários pelo menos 2 jogadores válidos para comparação.",
            "players": profiles,
        }

    p1, p2 = profiles[0], profiles[1]
    s1, s2 = p1["summary"], p2["summary"]

    deltas = {
        "performance_score_diff": round(p1["performance_score"] - p2["performance_score"], 2),
        "matches_diff": s1["total_matches"] - s2["total_matches"],
        "goals_diff": s1["total_goals"] - s2["total_goals"],
        "goals_per_match_diff": round(s1["goals_per_match"] - s2["goals_per_match"], 2),
        "assists_diff": s1["total_assists"] - s2["total_assists"],
        "assists_per_match_diff": round(s1["assists_per_match"] - s2["assists_per_match"], 2),
        "avg_rating_diff": round(s1["avg_rating"] - s2["avg_rating"], 2),
        "tackles_diff": (
            p1["telemetry"]["defending"]["tackles_made"]
            - p2["telemetry"]["defending"]["tackles_made"]
        ),
        "saves_diff": (
            p1["telemetry"]["goalkeeping"]["saves"]
            - p2["telemetry"]["goalkeeping"]["saves"]
        ),
    }

    return {
        "compared_count": len(profiles),
        "player_a": p1,
        "player_b": p2,
        "deltas_a_minus_b": deltas,
        "all_players": profiles,
    }


def get_team_of_the_week(db: Session, days: int = 7) -> dict[str, Any]:
    """Gera a Seleção da Semana (TOTW) no esquema 3-1-4-2 estilo SpotXI."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Partidas do período
    rows = (
        db.query(PlayerMatchStats, Match.played_at)
        .join(Match, Match.match_id == PlayerMatchStats.match_id)
        .filter(
            Match.played_at >= cutoff,
            Match.review_status.in_(COUNTED_STATUSES),
        )
        .all()
    )

    if not rows:
        # Fallback sem cutoff se a base for estática / de teste
        rows = (
            db.query(PlayerMatchStats, Match.played_at)
            .join(Match, Match.match_id == PlayerMatchStats.match_id)
            .filter(Match.review_status.in_(COUNTED_STATUSES))
            .all()
        )

    # Agrupar estatísticas da semana por atleta
    weekly_stats: dict[str, dict[str, Any]] = {}
    for pms, _ in rows:
        pk = pms.player_key
        if pk not in weekly_stats:
            weekly_stats[pk] = {
                "player_key": pk,
                "name": pms.playername,
                "club_id": pms.club_id,
                "positions": [],
                "matches": 0,
                "goals": 0,
                "assists": 0,
                "tackles": 0,
                "saves": 0,
                "ratings": [],
            }
        ws = weekly_stats[pk]
        ws["matches"] += 1
        ws["goals"] += pms.goals or 0
        ws["assists"] += pms.assists or 0
        ws["tackles"] += pms.tackles_made or 0
        ws["saves"] += pms.saves or 0
        if pms.position:
            ws["positions"].append(pms.position.upper())
        if pms.rating is not None:
            ws["ratings"].append(pms.rating)

    # Consolidar posição e performance
    candidates: list[dict[str, Any]] = []
    for pk, ws in weekly_stats.items():
        if ws["matches"] <= 0:
            continue
        avg_rating = round(sum(ws["ratings"]) / len(ws["ratings"]), 2) if ws["ratings"] else 6.0
        pos_mode = Counter(ws["positions"]).most_common(1)[0][0] if ws["positions"] else "MID"

        score = calculate_performance_score(
            pos_mode,
            {
                "goals": ws["goals"],
                "assists": ws["assists"],
                "tackles_made": ws["tackles"],
                "saves": ws["saves"],
                "avg_rating": avg_rating,
            },
            matches=ws["matches"],
        )

        candidates.append({
            "player_key": pk,
            "name": ws["name"],
            "club_id": ws["club_id"],
            "position": pos_mode,
            "matches": ws["matches"],
            "goals": ws["goals"],
            "assists": ws["assists"],
            "tackles": ws["tackles"],
            "tackles_per_match": round(ws["tackles"] / ws["matches"], 2),
            "saves": ws["saves"],
            "avg_rating": avg_rating,
            "score": score,
            "ga_sum": ws["goals"] + ws["assists"],
        })

    # Separar por categorias
    gks = [c for c in candidates if c["position"] in ["GK", "GOALKEEPER"]]
    defs = [c for c in candidates if c["position"] in ["CB", "LB", "RB", "LWB", "RWB", "DEF", "DEFENDER"]]
    mids = [c for c in candidates if c["position"] in ["CAM", "CM", "CDM", "LM", "RM", "MID", "MIDFIELDER"]]
    fwds = [c for c in candidates if c["position"] in ["ST", "CF", "LW", "RW", "FORWARD"]]

    # Fallbacks inteligentes caso faltem posições cadastradas no recorte
    pool = sorted(candidates, key=lambda x: (x["score"], x["avg_rating"]), reverse=True)
    if not gks and pool:
        gks = [pool[0]]
    if len(defs) < 3 and len(pool) > 3:
        defs = pool[1:4]
    if len(mids) < 5 and len(pool) > 8:
        mids = pool[4:9]
    if len(fwds) < 2 and len(pool) > 10:
        fwds = pool[9:11]

    # Ordenar por melhores
    gks.sort(key=lambda x: (x["saves"], x["score"]), reverse=True)
    defs.sort(key=lambda x: (x["tackles"], x["score"]), reverse=True)
    mids.sort(key=lambda x: (x["assists"], x["score"]), reverse=True)
    fwds.sort(key=lambda x: (x["goals"], x["score"]), reverse=True)

    # Esquema 3-1-4-2:
    # 1 GK
    chosen_gk = gks[0] if gks else None

    # 3 DEF
    chosen_defs = defs[:3]

    # 1 CDM isolado: o médio com mais desarmes ganhos por jogo
    if mids:
        chosen_cdm = max(mids, key=lambda m: m["tackles_per_match"])
        remaining_mids = [m for m in mids if m["player_key"] != chosen_cdm["player_key"]]
    else:
        chosen_cdm = None
        remaining_mids = []

    # 4 MID restantes
    chosen_mids = remaining_mids[:4]

    # 2 FWD
    chosen_fwds = fwds[:2]

    lineup_11 = [p for p in [chosen_gk, *chosen_defs, chosen_cdm, *chosen_mids, *chosen_fwds] if p]

    # Jogador da Semana (quem soma mais gols + assistências; desempate pelo score)
    potw = (
        max(lineup_11, key=lambda x: (x["ga_sum"], x["score"]))
        if lineup_11
        else (pool[0] if pool else None)
    )

    return {
        "formation": "3-1-4-2",
        "period_days": days,
        "player_of_the_week": potw,
        "starting_eleven": {
            "goalkeeper": chosen_gk,
            "defenders": chosen_defs,
            "defensive_midfielder": chosen_cdm,
            "midfielders": chosen_mids,
            "forwards": chosen_fwds,
        },
        "total_analyzed_players": len(candidates),
    }


def get_club_lineups(
    db: Session,
    club_id: str,
    min_matches: int = 5,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """Identifica panelinhas / grupos de 5+ jogadores que mais jogaram juntos pelo clube."""
    from itertools import combinations
    from app.db.models import ClubMatch

    # Buscar partidas do clube com seus resultados
    club_matches = (
        db.query(ClubMatch.match_id, ClubMatch.is_win, ClubMatch.is_draw, ClubMatch.is_loss)
        .filter(ClubMatch.club_id == club_id, ClubMatch.is_ignored == 0)
        .all()
    )

    if not club_matches:
        return []

    match_results = {cm.match_id: {"is_win": cm.is_win, "is_draw": cm.is_draw, "is_loss": cm.is_loss} for cm in club_matches}
    match_ids = list(match_results.keys())

    # Buscar os elencos escalados em cada partida (jogadores com >= 15 minutos)
    stats_rows = (
        db.query(PlayerMatchStats.match_id, PlayerMatchStats.player_key, PlayerMatchStats.playername)
        .filter(
            PlayerMatchStats.match_id.in_(match_ids),
            PlayerMatchStats.club_id == club_id,
            PlayerMatchStats.seconds_played >= 900,
        )
        .all()
    )

    roster_by_match: dict[str, set[tuple[str, str]]] = {}
    for r in stats_rows:
        if r.match_id not in roster_by_match:
            roster_by_match[r.match_id] = set()
        roster_by_match[r.match_id].add((r.player_key, r.playername))

    # Contabilizar combinações de 5 atletas
    combo_counts: Counter[tuple[tuple[str, str], ...]] = Counter()
    combo_wins: Counter[tuple[tuple[str, str], ...]] = Counter()

    for mid, roster in roster_by_match.items():
        if len(roster) >= 5:
            sorted_roster = sorted(list(roster), key=lambda x: x[0])
            for combo in combinations(sorted_roster, 5):
                combo_counts[combo] += 1
                if match_results.get(mid, {}).get("is_win", 0) == 1:
                    combo_wins[combo] += 1

    results: list[dict[str, Any]] = []
    for combo, total in combo_counts.most_common(50):
        if total >= min_matches:
            wins = combo_wins[combo]
            win_rate = round((wins / total) * 100.0, 1)
            players_list = [{"player_key": pk, "name": name} for pk, name in combo]

            # Evitar variantes idênticas (se compartilha >= 4 jogadores com um grupo já aceito)
            is_variant = False
            combo_keys = {pk for pk, _ in combo}
            for existing in results:
                existing_keys = {p["player_key"] for p in existing["players"]}
                if len(combo_keys.intersection(existing_keys)) >= 4:
                    is_variant = True
                    break

            if not is_variant:
                results.append({
                    "matches_together": total,
                    "wins": wins,
                    "win_rate_pct": win_rate,
                    "players": players_list,
                })

            if len(results) >= top_n:
                break

    return results
