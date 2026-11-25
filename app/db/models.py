from __future__ import annotations

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class Club(Base):
    __tablename__ = "clubs"

    club_id = Column(String, primary_key=True)
    platform = Column(String, nullable=False, default="common-gen5", primary_key=True)
    name = Column(String, nullable=False)
    team_id = Column(String)
    crest_asset_id = Column(String)
    crest_url = Column(String)
    stadium_name = Column(String)
    region_id = Column(String)
    first_seen_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    club_matches: list[ClubMatch] = relationship("ClubMatch", back_populates="club", foreign_keys="ClubMatch.club_id")

    def __repr__(self) -> str:
        return f"<Club club_id={self.club_id!r} name={self.name!r} platform={self.platform!r}>"


class Match(Base):
    __tablename__ = "matches"

    match_id = Column(String, primary_key=True)
    platform = Column(String, nullable=False, default="common-gen5")
    match_type_code = Column(String, nullable=False)
    played_at = Column(String, nullable=False)
    season_id = Column(String)
    game_number = Column(String)
    duration_seconds = Column(Integer)
    dnf = Column(Integer, nullable=False, default=0)
    winner_by_dnf = Column(String)
    club_a_id = Column(String, nullable=False)
    club_b_id = Column(String, nullable=False)
    club_a_goals = Column(Integer, nullable=False)
    club_b_goals = Column(Integer, nullable=False)
    raw_payload = Column(Text, nullable=False)
    payload_hash = Column(String, nullable=False)
    parser_version = Column(String, nullable=False)
    ingested_at = Column(String, nullable=False)
    first_seen_at = Column(String, nullable=False)
    last_seen_at = Column(String, nullable=False)
    seen_count = Column(Integer, nullable=False, default=1)

    # Revisão do resultado (ver app.core.review)
    # valid | needs_review | ignored | corrected
    review_status = Column(String, nullable=False, default="valid")
    review_reason = Column(String)
    review_note = Column(Text)
    reviewed_at = Column(String)
    # ea | manual | screenshot
    result_source = Column(String, nullable=False, default="ea")
    # Placar original da EA, preservado mesmo após uma correção manual.
    ea_club_a_goals = Column(Integer)
    ea_club_b_goals = Column(Integer)

    club_matches: list[ClubMatch] = relationship("ClubMatch", back_populates="match")
    team_stats: list[MatchTeamStats] = relationship("MatchTeamStats", back_populates="match")
    player_stats: list[PlayerMatchStats] = relationship("PlayerMatchStats", back_populates="match")
    classification: MatchClassification | None = relationship(
        "MatchClassification", back_populates="match", uselist=False
    )
    evidence: list[MatchEvidence] = relationship(
        "MatchEvidence", back_populates="match", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Match match_id={self.match_id!r} "
            f"{self.club_a_id}({self.club_a_goals}) vs "
            f"{self.club_b_id}({self.club_b_goals}) at {self.played_at}>"
        )


class ClubMatch(Base):
    __tablename__ = "club_matches"

    club_id = Column(String, ForeignKey("clubs.club_id"), primary_key=True)
    match_id = Column(String, ForeignKey("matches.match_id"), primary_key=True)
    platform = Column(String, nullable=False, default="common-gen5")
    goals_for = Column(Integer, nullable=False)
    goals_against = Column(Integer, nullable=False)
    is_win = Column(Integer, nullable=False)
    is_draw = Column(Integer, nullable=False)
    is_loss = Column(Integer, nullable=False)
    # 1 quando a partida não deve entrar nas estatísticas (resultado ignorado
    # ou aguardando revisão). Ver app.core.review.
    is_ignored = Column(Integer, nullable=False, default=0)

    club: Club = relationship("Club", back_populates="club_matches")
    match: Match = relationship("Match", back_populates="club_matches")

    def __repr__(self) -> str:
        result = "W" if self.is_win else ("D" if self.is_draw else "L")
        return f"<ClubMatch club={self.club_id} match={self.match_id} result={result} {self.goals_for}-{self.goals_against}>"


class MatchTeamStats(Base):
    __tablename__ = "match_team_stats"

    match_id = Column(String, ForeignKey("matches.match_id"), primary_key=True)
    club_id = Column(String, primary_key=True)
    source = Column(String, nullable=False, default="ea_aggregate")
    goals = Column(Integer)
    shots = Column(Integer)
    shots_on_target = Column(Integer)
    passes_made = Column(Integer)
    pass_attempts = Column(Integer)
    tackles_made = Column(Integer)
    tackle_attempts = Column(Integer)
    saves = Column(Integer)
    goals_conceded = Column(Integer)
    fouls = Column(Integer)
    corners = Column(Integer)
    possession_pct = Column(Float)
    raw_aggregate = Column(Text, nullable=False)

    match: Match = relationship("Match", back_populates="team_stats")

    def __repr__(self) -> str:
        return f"<MatchTeamStats match={self.match_id} club={self.club_id} goals={self.goals}>"


class Player(Base):
    __tablename__ = "players"

    player_key = Column(String, primary_key=True)
    ea_player_id = Column(String)
    platform = Column(String, nullable=False, default="common-gen5")
    first_seen_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    names: list[PlayerName] = relationship("PlayerName", back_populates="player", uselist=True)
    match_stats: list[PlayerMatchStats] = relationship("PlayerMatchStats", back_populates="player")

    def __repr__(self) -> str:
        return f"<Player player_key={self.player_key!r} ea_id={self.ea_player_id!r}>"


class PlayerName(Base):
    __tablename__ = "player_names"

    player_key = Column(String, ForeignKey("players.player_key"), primary_key=True)
    playername = Column(String, primary_key=True)
    first_seen_at = Column(String, nullable=False)
    last_seen_at = Column(String, nullable=False)

    player: Player = relationship("Player", back_populates="names")

    def __repr__(self) -> str:
        return f"<PlayerName player_key={self.player_key!r} name={self.playername!r}>"


class PlayerMatchStats(Base):
    __tablename__ = "player_match_stats"

    match_id = Column(String, ForeignKey("matches.match_id"), primary_key=True)
    club_id = Column(String, primary_key=False)
    player_key = Column(String, ForeignKey("players.player_key"), primary_key=True)
    playername = Column(String, nullable=False)
    position = Column(String)
    rating = Column(Float)
    seconds_played = Column(Integer)
    game_time = Column(Integer)
    goals = Column(Integer, default=0)
    assists = Column(Integer, default=0)
    shots = Column(Integer, default=0)
    passes_made = Column(Integer, default=0)
    pass_attempts = Column(Integer, default=0)
    tackles_made = Column(Integer, default=0)
    tackle_attempts = Column(Integer, default=0)
    saves = Column(Integer, default=0)
    goals_conceded = Column(Integer, default=0)
    clean_sheets_any = Column(Integer, default=0)
    clean_sheets_def = Column(Integer, default=0)
    clean_sheets_gk = Column(Integer, default=0)
    is_mom = Column(Integer, default=0)
    red_cards = Column(Integer, default=0)
    pro_overall = Column(Integer)
    archetype_id = Column(String)
    raw_player = Column(Text, nullable=False)

    match: Match = relationship("Match", back_populates="player_stats")
    player: Player = relationship("Player", back_populates="match_stats")

    def __repr__(self) -> str:
        return (
            f"<PlayerMatchStats match={self.match_id} player={self.player_key!r} "
            f"name={self.playername!r} rating={self.rating} goals={self.goals}>"
        )


class PlayerMatchEvent(Base):
    __tablename__ = "player_match_events"

    match_id = Column(String, ForeignKey("matches.match_id"), primary_key=True)
    player_key = Column(String, ForeignKey("players.player_key"), primary_key=True)
    event_id = Column(Integer, primary_key=True)
    count = Column(Integer, nullable=False)
    bucket = Column(Integer, nullable=False, primary_key=True)

    def __repr__(self) -> str:
        return f"<PlayerMatchEvent match={self.match_id} player={self.player_key} event={self.event_id} bucket={self.bucket} count={self.count}>"


class PlayerMatchMetrics(Base):
    __tablename__ = "player_match_metrics"

    match_id = Column(String, ForeignKey("matches.match_id"), primary_key=True)
    player_key = Column(String, ForeignKey("players.player_key"), primary_key=True)

    passes_completed = Column(Integer)
    passes_failed = Column(Integer)
    passes_completed_forward = Column(Integer)
    passes_completed_backward = Column(Integer)
    passes_completed_sideways = Column(Integer)
    passes_completed_short = Column(Integer)
    passes_completed_medium = Column(Integer)
    passes_completed_long = Column(Integer)
    passes_completed_cross = Column(Integer)
    passes_failed_forward = Column(Integer)
    passes_failed_backward = Column(Integer)
    passes_failed_sideways = Column(Integer)
    passes_failed_short = Column(Integer)
    passes_failed_medium = Column(Integer)
    passes_failed_long = Column(Integer)
    passes_failed_cross = Column(Integer)
    first_time_passes = Column(Integer)
    switches_of_play = Column(Integer)
    flair_passes = Column(Integer)
    through_passes = Column(Integer)
    offside_passes = Column(Integer)
    shots_on_target = Column(Integer)
    shots_off_target = Column(Integer)
    shots_on_target_inside_box = Column(Integer)
    shots_on_target_outside_box = Column(Integer)
    shots_off_target_inside_box = Column(Integer)
    shots_off_target_outside_box = Column(Integer)
    shots_saved = Column(Integer)
    goals_by_events = Column(Integer)
    goals_first_time = Column(Integer)
    goals_weak_foot = Column(Integer)
    goals_off_post = Column(Integer)
    second_assists = Column(Integer)
    through_ball_assists = Column(Integer)
    dribbles_completed = Column(Integer)
    dribbles_carried = Column(Integer)
    dribbles_beat = Column(Integer)
    dribbles_skill_move_beat = Column(Integer)
    tackles_won = Column(Integer)
    tackles_lost = Column(Integer)
    standing_tackles_won = Column(Integer)
    sliding_tackles_won = Column(Integer)
    clean_tackles = Column(Integer)
    dangerous_tackles = Column(Integer)
    interceptions = Column(Integer)
    opponents_dispossessed = Column(Integer)
    crosses_blocked = Column(Integer)
    aerial_duels_won_attacking = Column(Integer)
    aerial_duels_won_defending = Column(Integer)
    good_direction_saves = Column(Integer)
    possession_lost_defensive_third = Column(Integer)
    possession_lost_middle_third = Column(Integer)
    possession_lost_attacking_third = Column(Integer)
    possession_won_defensive_third = Column(Integer)
    possession_won_middle_third = Column(Integer)
    possession_won_attacking_third = Column(Integer)
    fouls_conceded = Column(Integer)
    fouls_conceded_defensive_third = Column(Integer)
    fouls_won = Column(Integer)
    yellow_cards = Column(Integer)
    yellow_cards_immediate = Column(Integer)
    yellow_cards_delayed = Column(Integer)
    penalties_conceded = Column(Integer)
    corners_conceded = Column(Integer)
    corners_taken = Column(Integer)
    in_position = Column(Integer)
    out_of_position = Column(Integer)
    oop_severity_1 = Column(Integer)
    oop_severity_2 = Column(Integer)
    oop_severity_3 = Column(Integer)
    oop_severity_4 = Column(Integer)
    oop_severity_5 = Column(Integer)
    feedback_use_the_ball = Column(Integer)
    feedback_pick_your_pass = Column(Integer)
    feedback_no_good_option = Column(Integer)
    feedback_good_option_taken = Column(Integer)
    feedback_best_option_taken = Column(Integer)
    feedback_should_have_passed_elsewhere = Column(Integer)
    feedback_chose_to_pass = Column(Integer)
    metrics_version = Column(String, nullable=False, default="events-3.0")

    def __repr__(self) -> str:
        return f"<PlayerMatchMetrics match={self.match_id} player={self.player_key}>"


class ClubOverallStats(Base):
    """Season snapshot from EA's ``clubs/overallStats`` endpoint.

    This is club-level, whole-season data — it is deliberately kept apart from
    the local match sample so the UI can label the two sources.
    """

    __tablename__ = "club_overall_stats"

    club_id = Column(String, primary_key=True)
    platform = Column(String, primary_key=True, default="common-gen5")
    games_played = Column(Integer)
    wins = Column(Integer)
    ties = Column(Integer)
    losses = Column(Integer)
    goals = Column(Integer)
    goals_against = Column(Integer)
    promotions = Column(Integer)
    relegations = Column(Integer)
    best_division = Column(Integer)
    best_finish_group = Column(Integer)
    skill_rating = Column(Integer)
    reputation_tier = Column(Integer)
    win_streak = Column(Integer)
    unbeaten_streak = Column(Integer)
    league_appearances = Column(Integer)
    playoff_games = Column(Integer)
    # JSON: [{"division": 3, "group": 1, "count": 2}, ...]
    division_finishes = Column(Text)
    # JSON: [{"result": 1, "opponent": "1569491"}, ...] (most recent first)
    last_results = Column(Text)
    raw_json = Column(Text, nullable=False)
    updated_at = Column(String, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<ClubOverallStats club={self.club_id} games={self.games_played} "
            f"sr={self.skill_rating}>"
        )


class ClubMember(Base):
    """Current-season member stats from EA's ``members/stats`` endpoint."""

    __tablename__ = "club_members"

    club_id = Column(String, primary_key=True)
    platform = Column(String, primary_key=True, default="common-gen5")
    name = Column(String, primary_key=True)
    # Linked local player (same person in match payloads); nullable until mapped.
    player_key = Column(String, ForeignKey("players.player_key"), index=True)
    pro_name = Column(String)
    position = Column(String)
    pro_pos = Column(Integer)
    overall = Column(Integer)
    games_played = Column(Integer)
    win_rate = Column(Integer)
    goals = Column(Integer)
    assists = Column(Integer)
    passes_made = Column(Integer)
    pass_success_rate = Column(Integer)
    shot_success_rate = Column(Integer)
    tackle_success_rate = Column(Integer)
    tackles_made = Column(Integer)
    rating_avg = Column(Float)
    man_of_the_match = Column(Integer)
    red_cards = Column(Integer)
    clean_sheets_def = Column(Integer)
    clean_sheets_gk = Column(Integer)
    height = Column(Integer)
    nationality = Column(String)
    # JSON list with the last 11 goals-per-match values (EA's prevGoals0..10)
    prev_goals = Column(Text)
    raw_json = Column(Text, nullable=False)
    updated_at = Column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<ClubMember club={self.club_id} name={self.name!r} ovr={self.overall}>"


class ClubMemberCareer(Base):
    """Career totals from EA's ``members/career/stats`` endpoint."""

    __tablename__ = "club_member_careers"

    club_id = Column(String, primary_key=True)
    platform = Column(String, primary_key=True, default="common-gen5")
    name = Column(String, primary_key=True)
    player_key = Column(String, ForeignKey("players.player_key"), index=True)
    position = Column(String)
    pro_pos = Column(Integer)
    games_played = Column(Integer)
    goals = Column(Integer)
    assists = Column(Integer)
    man_of_the_match = Column(Integer)
    rating_avg = Column(Float)
    prev_goals = Column(Integer)
    raw_json = Column(Text, nullable=False)
    updated_at = Column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<ClubMemberCareer club={self.club_id} name={self.name!r}>"


class PlayerAlias(Base):
    """Manual link between an EA member name and a local player_key.

    EA's member endpoint has no player id, so the link is made by name. This
    table lets the user fix a name that changed or that EA spells differently.
    """

    __tablename__ = "player_aliases"

    alias = Column(String, primary_key=True)  # normalised (see member_link)
    display_alias = Column(String)
    player_key = Column(
        String, ForeignKey("players.player_key"), nullable=False, index=True
    )
    created_at = Column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<PlayerAlias alias={self.alias!r} player={self.player_key!r}>"


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    club_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default="running")
    started_at = Column(String, nullable=False)
    finished_at = Column(String)
    http_status = Column(Integer)
    matches_returned = Column(Integer)
    matches_inserted = Column(Integer, default=0)
    matches_updated = Column(Integer, default=0)
    error = Column(Text)

    def __repr__(self) -> str:
        return f"<IngestRun id={self.id} club={self.club_id} status={self.status!r} started={self.started_at}>"


class MatchPayloadArchive(Base):
    """Append-only archive of every distinct raw EA payload ever captured.

    This is the historical safety net: even if the parser changes, a match row
    is corrected, or a bug drops columns, the original EA response stays here
    and the derived data can be rebuilt later.
    """

    __tablename__ = "match_payload_archive"

    match_id = Column(String, primary_key=True)
    payload_hash = Column(String, primary_key=True)
    parser_version = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    captured_at = Column(String, nullable=False)
    first_seen_at = Column(String, nullable=False)

    def __repr__(self) -> str:
        return f"<MatchPayloadArchive match={self.match_id} hash={self.payload_hash[:8]}...>"


class MatchEvidence(Base):
    """Print/foto enviado para comprovar ou corrigir o resultado de uma partida.

    O arquivo fica em disco (``EVIDENCE_DIR``) e o JSON extraído é guardado aqui
    para auditoria. A correção em si é aplicada em ``Match``/``ClubMatch``.
    """

    __tablename__ = "match_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    match_id = Column(
        String,
        ForeignKey("matches.match_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stored_path = Column(String, nullable=False)
    original_name = Column(String)
    content_type = Column(String)
    size_bytes = Column(Integer)
    sha256 = Column(String)
    # vision | ocr | none
    extractor = Column(String, nullable=False, default="none")
    extracted_json = Column(Text)
    created_at = Column(String, nullable=False)

    match: Match = relationship("Match", back_populates="evidence")

    def __repr__(self) -> str:
        return f"<MatchEvidence id={self.id} match={self.match_id} extractor={self.extractor!r}>"


class Championship(Base):
    """An external, privately organised competition.

    EA only knows about friendlies; this entity gives those friendlies an
    external context (league, cup, group stage, ...). Fields that people tend
    to customise (stage, round, tags, notes) live on the classification, while
    anything truly free-form can go into ``extra`` (JSON text).
    """

    __tablename__ = "championships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    # league | knockout | groups | mixed | other
    format = Column(String, nullable=False, default="league")
    # active | finished | archived
    status = Column(String, nullable=False, default="active")
    start_date = Column(String)
    end_date = Column(String)
    color = Column(String, default="#3b82f6")
    description = Column(Text)
    external_url = Column(String)
    extra = Column(Text)  # free-form JSON object
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    classifications: list[MatchClassification] = relationship(
        "MatchClassification", back_populates="championship"
    )

    def __repr__(self) -> str:
        return f"<Championship id={self.id} name={self.name!r} status={self.status!r}>"


class MatchClassification(Base):
    """Link a match to at most one championship (1:1, match_id is the PK)."""

    __tablename__ = "match_classifications"

    match_id = Column(
        String,
        ForeignKey("matches.match_id", ondelete="CASCADE"),
        primary_key=True,
    )
    championship_id = Column(
        Integer,
        ForeignKey("championships.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stage = Column(String)
    round = Column(String)
    leg = Column(String)  # ida | volta | unico | None
    group_name = Column(String)
    notes = Column(Text)
    tags = Column(Text)  # JSON array of strings
    updated_at = Column(String, nullable=False)

    championship: Championship = relationship("Championship", back_populates="classifications")
    match: Match = relationship("Match", back_populates="classification")

    def __repr__(self) -> str:
        return (
            f"<MatchClassification match={self.match_id} "
            f"championship={self.championship_id} stage={self.stage!r}>"
        )


class User(Base):
    """User account with email/password authentication and subscription tier."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    salt = Column(String(64), nullable=False)
    plan_tier = Column(String(32), nullable=False, default="free")  # "free" | "pro"
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    tracked_clubs: list[TrackedClub] = relationship(
        "TrackedClub", back_populates="user", cascade="all, delete-orphan", uselist=True
    )
    sessions: list[UserSession] = relationship(
        "UserSession", back_populates="user", cascade="all, delete-orphan", uselist=True
    )
    payment_orders: list[PaymentOrder] = relationship(
        "PaymentOrder", back_populates="user", cascade="all, delete-orphan", uselist=True
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} plan={self.plan_tier!r}>"


class TrackedClub(Base):
    """Clubs monitored by an account.

    Only clubs linked to a user account will be actively monitored and ingested.
    """

    __tablename__ = "tracked_clubs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    club_id = Column(String(64), nullable=False, index=True)
    platform = Column(String(32), nullable=False, default="common-gen5")
    club_name = Column(String(128), nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(String, nullable=False)

    user: User = relationship("User", back_populates="tracked_clubs")

    def __repr__(self) -> str:
        return f"<TrackedClub id={self.id} user_id={self.user_id} club_id={self.club_id!r}>"


class UserSession(Base):
    """Active web or API session token."""

    __tablename__ = "user_sessions"

    token = Column(String(64), primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(String, nullable=False)
    expires_at = Column(String, nullable=False)

    user: User = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        return f"<UserSession token={self.token[:8]}... user_id={self.user_id}>"


class PaymentOrder(Base):
    """Orders for subscription checkout (PIX / Gateway)."""

    __tablename__ = "payment_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount_cents = Column(Integer, nullable=False)  # 3990 = R$ 39,90
    currency = Column(String(10), nullable=False, default="BRL")
    plan_tier = Column(String(32), nullable=False, default="pro")
    gateway = Column(String(32), nullable=False, default="mock")  # mock | asaas | mercadopago
    status = Column(String(32), nullable=False, default="pending")  # pending | paid | expired
    pix_qr_code = Column(Text, nullable=True)
    pix_copy_paste = Column(Text, nullable=True)
    gateway_payment_id = Column(String(128), nullable=True)
    paid_at = Column(String, nullable=True)
    expires_at = Column(String, nullable=True)
    created_at = Column(String, nullable=False)

    user: User = relationship("User", back_populates="payment_orders")

    def __repr__(self) -> str:
        return f"<PaymentOrder order_id={self.order_id!r} status={self.status!r} amount={self.amount_cents}>"

