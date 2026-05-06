"""Strategic Agent Games: multi-agent negotiation arena."""

__version__ = "0.2.0"

# Core types
from arena.types import Action, ActionResult, AgentResponse, MessageIntent, TurnState

# Agent abstractions
from arena.agents import Agent, RandomAgent

# Experiment runner
from arena.experiment import ExperimentConfig, ExperimentResult, ExperimentRunner

# Game registry
from arena.games import get_game_spec, list_game_ids, register_game

# Built-in games
from arena.games.ultimatum import UltimatumGame
from arena.games.first_price_auction import FirstPriceAuctionGame
from arena.games.principal_agent import PrincipalAgentGame
from arena.games.bilateral_trade import BilateralTradeGame
from arena.games.provision_point import ProvisionPointGame
from arena.games.dictator import DictatorGame
from arena.games.public_project import PublicProjectGame

__all__ = [
    # Types
    "Action",
    "ActionResult",
    "AgentResponse",
    "MessageIntent",
    "TurnState",
    # Agents
    "Agent",
    "RandomAgent",
    # Experiment
    "ExperimentConfig",
    "ExperimentResult",
    "ExperimentRunner",
    # Registry
    "get_game_spec",
    "list_game_ids",
    "register_game",
    # Games
    "UltimatumGame",
    "FirstPriceAuctionGame",
    "PrincipalAgentGame",
    "BilateralTradeGame",
    "ProvisionPointGame",
    "DictatorGame",
    "PublicProjectGame"
]
