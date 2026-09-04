"""Registers the built-in model providers at start-up.

This is the only file that knows the full list. Adding a provider means adding
it here; nothing in the domain or application layers changes.
"""

from __future__ import annotations

import logging

from app.modules.model.domain.registry import PluginRegistry
from app.modules.model.domain.registry import registry as default_registry

logger = logging.getLogger(__name__)


def register_builtin_plugins(registry: PluginRegistry | None = None) -> PluginRegistry:
    from .asset_maintenance.plugin import (
        AssetBacktestPlugin,
        AssetDecisionPlugin,
        AssetEvidencePlugin,
    )
    from .curve_fit.plugin import CurveFitPlugin
    from .data_quality.plugin import DataQualityPlugin
    from .formula.plugin import FormulaModelPlugin
    from .join.plugin import JoinPlugin
    from .llm_reasoning.plugin import LlmReasoningPlugin
    from .monte_carlo.plugin import MonteCarloPlugin
    from .optimizer.plugin import OptimizerPlugin
    from .projection.plugin import ThresholdProjectionPlugin
    from .python_function.plugin import PythonTransformPlugin
    from .risk_matrix.plugin import RiskMatrixPlugin
    from .rule.plugin import RuleModelPlugin
    from .scorecard.plugin import ScorecardPlugin
    from .sklearn.plugin import SklearnModelPlugin
    from .typhoon_analog.backtest_plugin import TyphoonBacktestPlugin
    from .typhoon_analog.plugin import TyphoonAnalogPlugin
    from .typhoon_analog.precip_plugin import TyphoonPrecipAnalogPlugin

    target = registry or default_registry
    for plugin_cls in (
        FormulaModelPlugin,
        RuleModelPlugin,
        #  The first provider that reads two tables, which is what makes a
        #  pipeline graph able to merge rather than only branch.
        JoinPlugin,
        #  The three categories the library advertised and could not deliver.
        CurveFitPlugin,
        OptimizerPlugin,
        MonteCarloPlugin,
        #  The analysis vocabulary a decision needs after the statistics are
        #  computed: how good is it, how bad would it be, when does it reach
        #  the line, and can the data be trusted at all. None of them names a
        #  domain, and all four are read by more than one.
        DataQualityPlugin,
        ScorecardPlugin,
        RiskMatrixPlugin,
        ThresholdProjectionPlugin,
        #  The only provider that talks to something outside the process, and
        #  the only one that still answers when it cannot.
        LlmReasoningPlugin,
        PythonTransformPlugin,
        SklearnModelPlugin,
        TyphoonAnalogPlugin,
        TyphoonPrecipAnalogPlugin,
        TyphoonBacktestPlugin,
        #  The second built-in application: a condition assessment, its
        #  evidence, and the backtest that scores the decision policy.
        AssetDecisionPlugin,
        AssetEvidencePlugin,
        AssetBacktestPlugin,
    ):
        if target.has(plugin_cls().describe().key):
            continue
        target.register(plugin_cls())

    logger.info("registered model providers: %s", ", ".join(target.keys()))
    return target
