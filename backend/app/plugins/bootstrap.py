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
    from .curve_fit.plugin import CurveFitPlugin
    from .formula.plugin import FormulaModelPlugin
    from .join.plugin import JoinPlugin
    from .monte_carlo.plugin import MonteCarloPlugin
    from .optimizer.plugin import OptimizerPlugin
    from .python_function.plugin import PythonTransformPlugin
    from .rule.plugin import RuleModelPlugin
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
        PythonTransformPlugin,
        SklearnModelPlugin,
        TyphoonAnalogPlugin,
        TyphoonPrecipAnalogPlugin,
        TyphoonBacktestPlugin,
    ):
        if target.has(plugin_cls().describe().key):
            continue
        target.register(plugin_cls())

    logger.info("registered model providers: %s", ", ".join(target.keys()))
    return target
