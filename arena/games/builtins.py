"""Register built-in games (idempotent)."""

_registered = False


def ensure_builtins_registered() -> None:
    """Register all built-in games. Safe to call multiple times."""
    global _registered
    if _registered:
        return
    from arena.games import register_game
    from arena.games.ultimatum import UltimatumGame
    from arena.games.first_price_auction import FirstPriceAuctionGame
    from arena.games.principal_agent import PrincipalAgentGame
    from arena.games.bilateral_trade import BilateralTradeGame
    from arena.games.provision_point import ProvisionPointGame
    from arena.games.dictator import DictatorGame
    from arena.games.public_project import PublicProjectGame
    from arena.games.trust import TrustGame
    from arena.games.cournot import CournotGame
    from arena.games.all_pay_auction import AllPayAuctionGame
    from arena.games.hold_up import HoldUpGame
    from arena.games.war_of_attrition import WarOfAttritionGame
    from arena.games.dutch_auction import DutchAuctionGame
    from arena.games.english_auction import EnglishAuctionGame
    from arena.games.sequential_investment import SequentialInvestmentGame
    from arena.games.centipede import CentipedeGame
    from arena.games.voluntary_contribution import VoluntaryContributionGame
    from arena.games.insurance_moral_hazard import InsuranceMoralHazardGame
    from arena.games.principal_agent import PrincipalAgentGame

    register_game(UltimatumGame())
    register_game(FirstPriceAuctionGame())
    register_game(PrincipalAgentGame())
    register_game(BilateralTradeGame())
    register_game(ProvisionPointGame())
    register_game(DictatorGame())
    register_game(PublicProjectGame())
    register_game(TrustGame())
    register_game(CournotGame())
    register_game(AllPayAuctionGame())
    register_game(HoldUpGame())
    register_game(WarOfAttritionGame())
    register_game(DutchAuctionGame())
    register_game(EnglishAuctionGame())
    register_game(SequentialInvestmentGame())
    register_game(VoluntaryContributionGame())
    register_game(InsuranceMoralHazardGame())
    register_game(CentipedeGame())
    register_game(PrincipalAgentGame())

    _registered = True
