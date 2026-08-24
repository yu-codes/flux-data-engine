"""Scikit-learn provider - the trainable case.

This is the only plugin in the repository that imports an ML framework, and it
is deliberately confined to ``app/plugins``. The Model, Execution and Result
domains know nothing about scikit-learn: they see a provider that happens to
implement ``train()`` in addition to ``execute()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.modules.model.domain.entities import ModelDefinition, ModelType, RuntimeKind
from app.modules.model.domain.plugin import (
    ExecutionContext,
    ExecutionKind,
    ExecutionOutcome,
    PluginDescriptor,
    TrainingOutcome,
)
from app.shared.contracts import (
    Contract,
    ContractShape,
    FieldSpec,
    FieldType,
    ValidationResult,
)
from app.shared.errors import ExecutionError, ValidationError
from app.shared.payloads import ResultKind, ResultPayload
from app.shared.tabular import Table

PLUGIN_KEY = "sklearn"

ALGORITHMS = {
    "linear_regression": ("regression", "sklearn.linear_model", "LinearRegression"),
    "ridge": ("regression", "sklearn.linear_model", "Ridge"),
    "random_forest_regressor": ("regression", "sklearn.ensemble", "RandomForestRegressor"),
    "gradient_boosting_regressor": (
        "regression",
        "sklearn.ensemble",
        "GradientBoostingRegressor",
    ),
    "logistic_regression": ("classification", "sklearn.linear_model", "LogisticRegression"),
    "random_forest_classifier": (
        "classification", "sklearn.ensemble", "RandomForestClassifier",
    ),
    "decision_tree_classifier": (
        "classification",
        "sklearn.tree",
        "DecisionTreeClassifier",
    ),
}

ARTIFACT_NAME = "model.joblib"


def _build_estimator(algorithm: str, hyperparameters: dict[str, Any]):
    import importlib

    if algorithm not in ALGORITHMS:
        raise ValidationError(
            f"unknown algorithm '{algorithm}'", details={"available": sorted(ALGORITHMS)}
        )
    _, module_path, class_name = ALGORITHMS[algorithm]
    module = importlib.import_module(module_path)
    estimator_cls = getattr(module, class_name)
    return estimator_cls(**(hyperparameters or {}))


def _task_of(algorithm: str) -> str:
    return ALGORITHMS[algorithm][0]


class SklearnModelPlugin:
    """Trainable provider: train() produces a Model Version, execute() predicts."""

    def describe(self) -> PluginDescriptor:
        #  The library is what changes the answer, so the library's version is
        #  what the record needs - not a number this file maintains by hand.
        import sklearn

        return PluginDescriptor(
            key=PLUGIN_KEY,
            version=sklearn.__version__,
            name="Scikit-learn",
            model_type=ModelType.MACHINE_LEARNING,
            runtime=RuntimeKind.PYTHON,
            description=(
                "Supervised regression and classification. A training execution "
                "produces an immutable Model Version; prediction executions run "
                "against that version's artifact."
            ),
            trainable=True,
            supported_kinds=(ExecutionKind.PREDICTION, ExecutionKind.TRAINING),
            configuration_contract=Contract(
                shape=ContractShape.OBJECT,
                fields=[
                    FieldSpec("algorithm", FieldType.STRING,
                              enum=tuple(sorted(ALGORITHMS))),
                    FieldSpec("target", FieldType.STRING,
                              description="column to learn (training only)"),
                    FieldSpec("features", FieldType.ARRAY,
                              description="numeric feature columns"),
                    FieldSpec("test_size", FieldType.FLOAT, required=False, default=0.2),
                    FieldSpec("random_state", FieldType.INTEGER, required=False,
                              default=42),
                    FieldSpec("hyperparameters", FieldType.JSON, required=False),
                ],
            ),
            output_contract=Contract(
                shape=ContractShape.TABLE,
                description="input rows plus a 'prediction' column",
                fields=[FieldSpec("prediction", FieldType.ANY)],
            ),
            examples=[
                {
                    "name": "Revenue forecast",
                    "configuration": {
                        "algorithm": "random_forest_regressor",
                        "target": "revenue",
                        "features": ["price", "quantity"],
                    },
                }
            ],
        )

    # -- validation --------------------------------------------------------
    def validate(self, definition: ModelDefinition) -> ValidationResult:
        result = ValidationResult()
        config = definition.configuration or {}
        algorithm = config.get("algorithm")
        if algorithm not in ALGORITHMS:
            return result.add_error(
                f"configuration.algorithm must be one of {sorted(ALGORITHMS)}"
            )
        if not config.get("target"):
            result.add_error("configuration.target is required for a trainable model")
        features = config.get("features")
        if not isinstance(features, list) or not features:
            result.add_error("configuration.features must be a non-empty list")
        elif config.get("target") in features:
            result.add_error("the target column must not also be a feature")
        test_size = config.get("test_size", 0.2)
        if not isinstance(test_size, (int, float)) or not 0 < float(test_size) < 1:
            result.add_error("configuration.test_size must be between 0 and 1")
        return result

    # -- training ----------------------------------------------------------
    def train(self, context: ExecutionContext) -> TrainingOutcome:
        import joblib
        from sklearn.model_selection import train_test_split

        config = {**(context.definition.configuration or {}), **context.parameters}
        algorithm = str(config["algorithm"])
        target = str(config["target"])
        features = list(config["features"])

        frame = self._frame(context, required=[*features, target])
        frame = frame.dropna(subset=[*features, target])
        if len(frame) < 5:
            raise ValidationError(
                f"training needs at least 5 complete rows, got {len(frame)}"
            )

        x_all = frame[features]
        y_all = frame[target]
        test_size = float(config.get("test_size", 0.2))
        random_state = int(config.get("random_state", 42))

        x_train, x_test, y_train, y_test = train_test_split(
            x_all, y_all, test_size=test_size, random_state=random_state
        )
        estimator = _build_estimator(algorithm, config.get("hyperparameters") or {})
        estimator.fit(x_train, y_train)

        metrics = self._score(_task_of(algorithm), estimator, x_test, y_test)
        metrics.update(
            {"train_rows": int(len(x_train)), "test_rows": int(len(x_test))}
        )

        artifact = Path(context.workdir or ".") / ARTIFACT_NAME
        joblib.dump(
            {"estimator": estimator, "features": features, "target": target,
             "algorithm": algorithm},
            artifact,
        )
        context.log(f"trained {algorithm} on {len(x_train)} rows")

        return TrainingOutcome(
            artifact_path=str(artifact),
            parameters={
                "algorithm": algorithm,
                "target": target,
                "features": features,
                "hyperparameters": config.get("hyperparameters") or {},
                "test_size": test_size,
                "random_state": random_state,
            },
            metrics=metrics,
            payload=ResultPayload(
                kind=ResultKind.REPORT,
                value={"algorithm": algorithm, "target": target,
                       "features": features, "metrics": metrics},
                summary={"algorithm": algorithm, "rows": int(len(frame))},
            ),
            logs=context.logs,
        )

    # -- prediction --------------------------------------------------------
    def execute(self, context: ExecutionContext) -> ExecutionOutcome:
        import joblib

        if not context.artifact_path:
            raise ExecutionError(
                "this model has no trained version yet - run a training execution first"
            )
        bundle = joblib.load(context.artifact_path)
        estimator = bundle["estimator"]
        features = bundle["features"]

        frame = self._frame(context, required=features)
        usable = frame.dropna(subset=features)
        if usable.empty:
            raise ValidationError("no input rows have all the required feature columns")

        predictions = estimator.predict(usable[features])
        rows = usable.to_dict(orient="records")
        for row, prediction in zip(rows, predictions, strict=True):
            row["prediction"] = _scalar(prediction)

        table = Table.from_rows(rows)
        kind = (
            ResultKind.CLASSIFICATION
            if _task_of(bundle["algorithm"]) == "classification"
            else ResultKind.TABLE
        )
        return ExecutionOutcome(
            payload=ResultPayload.of_table(
                table,
                kind=kind,
                summary={"algorithm": bundle["algorithm"], "target": bundle["target"],
                         "features": features},
                materialise_as_dataset=True,
                dataset_name=f"{context.definition.name} predictions",
            ),
            metrics={"rows_predicted": table.num_rows,
                     "rows_skipped": int(len(frame) - len(usable))},
            logs=context.logs,
        )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _frame(context: ExecutionContext, *, required: list[str]):
        import pandas as pd

        if context.input.has_table:
            frame = context.input.table.to_pandas()
        elif context.input.record:
            frame = pd.DataFrame([context.input.record])
        else:
            raise ValidationError("this model needs a dataset or an input record")

        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValidationError(
                f"input is missing required column(s): {missing}",
                details={"available": list(frame.columns)},
            )
        for column in required:
            #  Coerce what can be numeric; leave genuinely categorical targets alone.
            converted = pd.to_numeric(frame[column], errors="coerce")
            if converted.notna().any():
                frame[column] = converted
        return frame

    @staticmethod
    def _score(task: str, estimator, x_test, y_test) -> dict[str, Any]:
        from sklearn import metrics as sk

        predicted = estimator.predict(x_test)
        if task == "regression":
            mse = float(sk.mean_squared_error(y_test, predicted))
            return {
                "r2": round(float(sk.r2_score(y_test, predicted)), 6),
                "rmse": round(mse ** 0.5, 6),
                "mae": round(float(sk.mean_absolute_error(y_test, predicted)), 6),
            }
        return {
            "accuracy": round(float(sk.accuracy_score(y_test, predicted)), 6),
            "f1_macro": round(
                float(sk.f1_score(y_test, predicted, average="macro", zero_division=0)), 6
            ),
        }


def _scalar(value: Any) -> Any:
    """numpy scalars are not JSON-serialisable; unwrap them."""
    item = getattr(value, "item", None)
    return item() if callable(item) else value
