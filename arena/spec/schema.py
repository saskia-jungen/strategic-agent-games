"""Game spec schema: machine-readable definition of a game's rules."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TurnOrder(str, Enum):
    """How turns are ordered within a phase."""

    ROUND_ROBIN = "round_robin"  # strict A, B, A, B, ...
    RANDOM = "random"  # runner picks a random agent each turn


class OutcomeRule(str, Enum):
    """How the final outcome is determined (high-level)."""

    AGREEMENT = "agreement"  # valid only if all accept (e.g. last offer)
    ENGINE = "engine"  # deterministic from state (e.g. highest bid wins)
    MEDIATOR = "mediator"  # external / rule-based pick from options


class ActionTypeDef(BaseModel):
    """Definition of an action type that can appear in a phase."""

    name: str = Field(..., description="e.g. submit_offer, place_bid, accept")
    description: str = Field(default="", description="Human-readable description")
    payload_schema: dict[str, Any] = Field(default_factory=dict, description="JSON schema for payload")
    is_message: bool = Field(default=False, description="True if this is send_public_message or send_private_message")


class Phase(BaseModel):
    """One phase of the game (e.g. discuss, then bid)."""

    name: str = Field(..., description="Phase id, e.g. discuss, bidding")
    turn_order: TurnOrder = Field(..., description="How agents take turns in this phase")
    allowed_action_types: list[str] = Field(
        default_factory=list,
        description="Action type names allowed in this phase (e.g. send_public_message, place_bid)",
    )
    max_rounds: int | None = Field(default=None, description="If round-based, max rounds (None = unbounded)")


class GameSpec(BaseModel):
    """Full game specification: phases, actions, outcome, payoffs."""

    game_id: str = Field(..., description="Unique game identifier")
    name: str = Field(..., description="Human-readable name")
    min_agents: int = Field(default=1, description="Minimum agents required before match can run (e.g. 2 for two-player games)")
    max_agents: int | None = Field(default=None, description="Maximum agents allowed. None means the game supports an unbounded number of players (n-player). When equal to min_agents, the game is fixed-size.")
    description: str = Field(default="", description="Rules in natural language (for get_game_rules)")
    phases: list[Phase] = Field(default_factory=list, description="Ordered phases")
    action_types: list[ActionTypeDef] = Field(default_factory=list, description="All action types used in this game")
    outcome_rule: OutcomeRule = Field(..., description="How outcome is determined")
    initial_game_state: dict[str, Any] = Field(default_factory=dict, description="Initial state (game-specific)")
