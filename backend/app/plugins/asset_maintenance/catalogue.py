"""The engineering knowledge this application ships with.

Everything domain-specific about rotating and static plant lives here, as data:
what each class of equipment measures, how those measurements respond to load
and to ambient conditions, which degradation modes it suffers, what each mode
does to which signal, and the maintenance policy the manufacturer publishes for
it.

It is written once and read by three unrelated things — the fleet simulator,
the seeded thresholds and policies, and the engineering rules the analysis
engine evaluates — which is the reason it is a module of tables rather than
constants scattered through those three.

The numbers are representative of industrial practice rather than copied from
any one manufacturer's datasheet: near-centre bearing temperatures a little
above ambient plus a load term, ISO 10816-class vibration limits in mm/s RMS,
transformer dissolved-hydrogen limits in ppm. They are the shape of the
physics, and the analysis is built to be re-pointed at a real datasheet by
editing this file and the seeded policy rows it produces.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Measurement:
    """One thing an asset class measures, and how it normally behaves.

        value ≈ intercept + ambient_coefficient × ambient
                          + load_coefficient × load_fraction
                          + noise

    The load and ambient terms are what make the analysis engine's job real:
    a current of 90 A means nothing until it is read against the load that
    produced it, and a bearing at 74 °C in a 38 °C plant room is not the same
    reading as 74 °C in a 22 °C one.
    """

    parameter: str
    label: str
    unit: str
    intercept: float
    load_coefficient: float = 0.0
    ambient_coefficient: float = 0.0
    noise: float = 0.1
    #  What the sensor reads when the machine is not turning. Not zero for
    #  everything: a stopped pump still has a discharge pressure of nearly
    #  nothing but a transformer still has an oil temperature.
    idle: float | None = 0.0
    idle_follows_ambient: bool = False
    #  Physically possible range. Anything outside is an instrument fault, and
    #  the data-quality layer is told so rather than left to infer it.
    physical_min: float = 0.0
    physical_max: float = 1e6
    decimals: int = 2
    #  Which way is bad. Some measurements fail high (temperature, vibration)
    #  and some fail low (oil pressure, flow).
    direction: str = "high"
    #  Warning / critical / emergency boundaries, as offsets from the value a
    #  healthy asset shows at 80% load in a 28 °C plant. Stated as offsets so
    #  one table serves eight asset classes without repeating their baselines.
    bands: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class FailureMode:
    """A way this class of equipment degrades, and what it does to the signals.

    `effects` is the whole point: a mode is recognised by a *pattern* across
    several measurements, not by one of them crossing a line. Bearing wear
    raises vibration and bearing temperature and, slightly, current; a blocked
    filter raises differential pressure and current while vibration stays
    where it was. Those two are indistinguishable from a temperature alarm and
    obvious from the pattern.
    """

    key: str
    label: str
    #  parameter -> the offset reached at the point of failure.
    effects: dict[str, float]
    symptom: str
    root_cause: str
    action: str
    parts: str
    #  Typical days from the first detectable change to functional failure.
    development_days: tuple[int, int] = (25, 70)
    severity: str = "major"


@dataclass(frozen=True)
class AssetClass:
    """Everything the platform knows about one kind of equipment."""

    key: str
    label: str
    measurements: tuple[Measurement, ...]
    modes: tuple[FailureMode, ...]
    #  Usage-based policy: task -> operating hours between occurrences.
    usage_policy: tuple[tuple[str, int, str], ...] = ()
    #  Time-based policy: task -> days between occurrences.
    time_policy: tuple[tuple[str, int, str], ...] = ()
    specifications: tuple[tuple[str, float, str], ...] = ()
    duty: str = "two_shift"
    design_life_years: int = 20
    mtbf_hours: int = 26000

    def measurement(self, parameter: str) -> Measurement | None:
        for item in self.measurements:
            if item.parameter == parameter:
                return item
        return None

    def mode(self, key: str) -> FailureMode | None:
        for item in self.modes:
            if item.key == key:
                return item
        return None


def _m(*args, **kwargs) -> Measurement:
    return Measurement(*args, **kwargs)


# --------------------------------------------------------------------------
# the classes
# --------------------------------------------------------------------------
PUMP = AssetClass(
    key="centrifugal_pump",
    label="離心泵浦",
    duty="two_shift",
    design_life_years=20,
    mtbf_hours=24000,
    specifications=(
        ("rated_power", 30.0, "kW"),
        ("rated_flow", 120.0, "m3/h"),
        ("rated_head", 65.0, "m"),
        ("rated_speed", 2950.0, "rpm"),
        ("rated_current", 52.0, "A"),
    ),
    measurements=(
        _m("vibration_rms", "振動速度", "mm/s", 2.2, 0.9, 0.0, 0.12, 0.15,
           physical_max=60, bands=(2.4, 4.2, 6.6)),
        _m("bearing_temperature", "軸承溫度", "°C", 12.0, 22.0, 1.0, 0.9, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=180,
           bands=(12.0, 22.0, 32.0)),
        _m("discharge_pressure", "出口壓力", "bar", 6.4, -0.5, 0.0, 0.06, 0.05,
           physical_max=40, direction="low", bands=(-0.7, -1.2, -1.8)),
        _m("flow_rate", "流量", "m3/h", 5.0, 115.0, 0.0, 1.5, 0.0,
           physical_max=400, direction="low", bands=(-9.0, -16.0, -24.0)),
        _m("motor_current", "電流", "A", 8.0, 38.0, 0.0, 0.5, 0.3,
           physical_max=200, bands=(3.0, 5.5, 8.0)),
        _m("power_kw", "功率", "kW", 3.0, 26.0, 0.0, 0.4, 0.1,
           physical_max=200, bands=(2.5, 4.5, 7.0)),
    ),
    modes=(
        FailureMode(
            "bearing_wear", "軸承磨損",
            {"vibration_rms": 6.5, "bearing_temperature": 14.0, "motor_current": 2.5},
            symptom="振動與軸承溫度同步上升",
            root_cause="軸承滾道疲勞剝落，潤滑膜破壞",
            action="更換驅動端軸承並重新加脂",
            parts="Bearing 6310-C3；潤滑脂 NLGI 2",
            development_days=(30, 75),
        ),
        FailureMode(
            "impeller_wear", "葉輪磨蝕／氣蝕",
            {"flow_rate": -18.0, "discharge_pressure": -1.6,
             "vibration_rms": 2.4, "power_kw": 2.0},
            symptom="流量與出口壓力同步下滑，功率不降反升",
            root_cause="葉輪前緣氣蝕剝蝕，內部再循環增加",
            action="拆檢葉輪、修補或更換並校正口環間隙",
            parts="Impeller；Wear ring",
            development_days=(45, 110),
        ),
        FailureMode(
            "seal_leak", "機械軸封洩漏",
            {"discharge_pressure": -0.9, "flow_rate": -6.0},
            symptom="出口壓力緩降並伴隨軸封處滲漏",
            root_cause="機械軸封端面磨損、O-ring 老化",
            action="更換機械軸封組件",
            parts="Mechanical seal 45mm；O-ring set",
            development_days=(20, 55),
            severity="minor",
        ),
    ),
    usage_policy=(
        ("軸承加脂", 3000, "lubrication"),
        ("軸承檢查與振動量測", 6000, "inspection"),
        ("軸承更換", 12000, "replacement"),
        ("機械軸封更換", 18000, "replacement"),
    ),
    time_policy=(("年度大修", 365, "overhaul"),),
)

MOTOR = AssetClass(
    key="electric_motor",
    label="電動馬達",
    duty="two_shift",
    design_life_years=20,
    mtbf_hours=32000,
    specifications=(
        ("rated_power", 45.0, "kW"),
        ("rated_voltage", 380.0, "V"),
        ("rated_current", 84.0, "A"),
        ("rated_speed", 1480.0, "rpm"),
        ("insulation_class", 155.0, "°C"),
    ),
    measurements=(
        _m("vibration_rms", "振動速度", "mm/s", 1.8, 0.7, 0.0, 0.10, 0.12,
           physical_max=60, bands=(2.0, 3.6, 5.8)),
        _m("bearing_temperature", "軸承溫度", "°C", 14.0, 20.0, 1.0, 0.9, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=180,
           bands=(11.0, 20.0, 30.0)),
        _m("winding_temperature", "繞組溫度", "°C", 20.0, 45.0, 1.0, 1.2, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=220,
           bands=(14.0, 26.0, 38.0)),
        _m("motor_current", "電流", "A", 6.0, 52.0, 0.0, 0.6, 0.4,
           physical_max=300, bands=(4.0, 7.5, 11.0)),
        _m("power_kw", "功率", "kW", 2.0, 42.0, 0.0, 0.5, 0.1,
           physical_max=300, bands=(3.5, 6.5, 10.0)),
        _m("rpm", "轉速", "rpm", 1492.0, -12.0, 0.0, 3.0, 0.0,
           physical_max=6000, direction="low", bands=(-12.0, -25.0, -45.0)),
    ),
    modes=(
        FailureMode(
            "bearing_wear", "軸承磨損",
            {"vibration_rms": 5.5, "bearing_temperature": 13.0, "motor_current": 3.0},
            symptom="振動上升並帶動軸承溫度",
            root_cause="軸承潤滑不足導致滾道剝落",
            action="更換兩端軸承，量測軸電流",
            parts="Bearing 6312；Bearing 6310",
            development_days=(30, 80),
        ),
        FailureMode(
            "winding_insulation", "繞組絕緣劣化",
            {"winding_temperature": 22.0, "motor_current": 5.0, "vibration_rms": 0.5},
            symptom="同負載下繞組溫度與電流同時偏高",
            root_cause="絕緣老化造成匝間局部短路、銅損增加",
            action="繞組絕緣電阻與極化指數量測，必要時重繞",
            parts="Stator rewind kit",
            development_days=(40, 120),
            severity="critical",
        ),
        FailureMode(
            "misalignment", "軸心偏移",
            {"vibration_rms": 4.2, "bearing_temperature": 6.0, "motor_current": 1.5},
            symptom="振動明顯上升但溫度僅小幅變化",
            root_cause="聯軸器對心不良、基座鬆動",
            action="雷射對心並重新鎖固基座",
            parts="Coupling insert；Shim set",
            development_days=(15, 45),
            severity="minor",
        ),
    ),
    usage_policy=(
        ("軸承加脂", 4000, "lubrication"),
        ("絕緣電阻量測", 8000, "inspection"),
        ("軸承更換", 16000, "replacement"),
    ),
    time_policy=(("年度電氣檢驗", 365, "inspection"),),
)

COMPRESSOR = AssetClass(
    key="air_compressor",
    label="空氣壓縮機",
    duty="continuous",
    design_life_years=15,
    mtbf_hours=20000,
    specifications=(
        ("rated_power", 75.0, "kW"),
        ("rated_pressure", 8.0, "bar"),
        ("rated_flow", 13.0, "m3/min"),
        ("rated_current", 132.0, "A"),
    ),
    measurements=(
        _m("vibration_rms", "振動速度", "mm/s", 2.6, 1.1, 0.0, 0.15, 0.2,
           physical_max=60, bands=(2.6, 4.5, 7.0)),
        _m("discharge_temperature", "排氣溫度", "°C", 45.0, 38.0, 0.9, 1.5, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=250,
           bands=(13.0, 24.0, 34.0)),
        _m("discharge_pressure", "排氣壓力", "bar", 7.2, 0.6, 0.0, 0.08, 0.1,
           physical_max=25, direction="low", bands=(-0.6, -1.1, -1.7)),
        _m("motor_current", "電流", "A", 12.0, 55.0, 0.0, 0.8, 0.5,
           physical_max=400, bands=(5.0, 9.0, 14.0)),
        _m("oil_pressure", "油壓", "bar", 3.4, 0.3, 0.0, 0.05, 0.05,
           physical_max=15, direction="low", bands=(-0.5, -0.9, -1.4)),
        _m("power_kw", "功率", "kW", 5.0, 48.0, 0.0, 0.6, 0.2,
           physical_max=300, bands=(4.0, 7.5, 11.0)),
    ),
    modes=(
        FailureMode(
            "lubrication_degradation", "潤滑劣化",
            {"oil_pressure": -1.5, "discharge_temperature": 12.0, "vibration_rms": 2.5},
            symptom="油壓下降伴隨排氣溫度上升",
            root_cause="油品氧化、油濾阻塞導致油膜不足",
            action="更換潤滑油與油濾，取樣送油品分析",
            parts="Compressor oil 20L；Oil filter",
            development_days=(20, 55),
        ),
        FailureMode(
            "valve_leak", "閥片洩漏",
            {"discharge_pressure": -1.3, "discharge_temperature": 14.0, "power_kw": 4.0},
            symptom="排氣壓力下降但功率與排氣溫度上升",
            root_cause="排氣閥片破損造成回流",
            action="更換閥片組並檢查閥座密合",
            parts="Valve plate kit",
            development_days=(25, 60),
        ),
        FailureMode(
            "bearing_wear", "軸承磨損",
            {"vibration_rms": 5.8, "discharge_temperature": 6.0, "motor_current": 3.0},
            symptom="振動持續上升",
            root_cause="主軸軸承磨損",
            action="更換主軸軸承",
            parts="Bearing set",
            development_days=(35, 90),
            severity="critical",
        ),
    ),
    usage_policy=(
        ("潤滑油更換", 4000, "lubrication"),
        ("空氣濾芯更換", 2000, "replacement"),
        ("閥片檢查", 8000, "inspection"),
        ("大修", 20000, "overhaul"),
    ),
    time_policy=(("壓力容器年檢", 365, "inspection"),),
)

VALVE = AssetClass(
    key="control_valve",
    label="控制閥",
    duty="two_shift",
    design_life_years=15,
    mtbf_hours=40000,
    specifications=(
        ("rated_pressure", 16.0, "bar"),
        ("rated_flow", 95.0, "m3/h"),
        ("nominal_diameter", 100.0, "mm"),
        ("stroke_time", 3.5, "s"),
    ),
    measurements=(
        _m("valve_position", "開度", "%", 20.0, 60.0, 0.0, 2.0, 0.0,
           physical_max=100, bands=(8.0, 15.0, 25.0)),
        _m("differential_pressure", "壓差", "bar", 1.2, 1.4, 0.0, 0.07, 0.02,
           physical_max=20, bands=(0.4, 0.8, 1.3)),
        _m("actuator_air_pressure", "驅動氣壓", "bar", 3.8, 0.4, 0.0, 0.05, 0.5,
           physical_max=12, bands=(0.5, 0.9, 1.4)),
        _m("stroke_time", "行程時間", "s", 3.2, 0.2, 0.0, 0.12, 0.0,
           physical_max=60, bands=(1.0, 2.2, 3.8)),
        _m("flow_rate", "流量", "m3/h", 4.0, 90.0, 0.0, 1.8, 0.0,
           physical_max=400, direction="low", bands=(-8.0, -15.0, -23.0)),
    ),
    modes=(
        FailureMode(
            "valve_sticking", "閥件卡澀",
            {"stroke_time": 5.5, "actuator_air_pressure": 1.2,
             "differential_pressure": 0.7},
            symptom="行程時間拉長，驅動氣壓需求上升",
            root_cause="填料箱摩擦增加、閥桿積垢",
            action="更換填料並清潔閥桿，重新整定定位器",
            parts="Packing set；Positioner kit",
            development_days=(25, 70),
        ),
        FailureMode(
            "seat_erosion", "閥座沖蝕",
            {"differential_pressure": -0.5, "flow_rate": 14.0},
            symptom="同開度下流量偏高、壓差偏低（內漏）",
            root_cause="閥座與閥塞沖蝕造成內漏",
            action="更換閥座與閥塞（trim）",
            parts="Trim set",
            development_days=(50, 140),
            severity="minor",
        ),
        FailureMode(
            "packing_leak", "填料洩漏",
            {"actuator_air_pressure": 0.9, "stroke_time": 1.2},
            symptom="驅動氣壓需求緩升",
            root_cause="填料壓縮量不足、彈簧疲乏",
            action="重新調整填料壓蓋或更換填料",
            parts="Packing set",
            development_days=(20, 60),
            severity="minor",
        ),
    ),
    usage_policy=(
        ("閥桿潤滑與行程測試", 4000, "inspection"),
        ("填料更換", 12000, "replacement"),
        ("Trim 檢查", 20000, "inspection"),
    ),
    time_policy=(("定位器校正", 180, "calibration"),),
)

GENERATOR = AssetClass(
    key="diesel_generator",
    label="柴油發電機",
    duty="standby",
    design_life_years=25,
    mtbf_hours=12000,
    specifications=(
        ("rated_power", 500.0, "kW"),
        ("rated_voltage", 380.0, "V"),
        ("rated_speed", 1800.0, "rpm"),
        ("fuel_capacity", 1000.0, "L"),
    ),
    measurements=(
        _m("coolant_temperature", "冷卻水溫", "°C", 55.0, 28.0, 0.6, 1.2, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=140,
           bands=(10.0, 18.0, 26.0)),
        _m("oil_pressure", "機油壓力", "bar", 4.2, 0.5, 0.0, 0.06, 0.0,
           physical_max=12, direction="low", bands=(-0.6, -1.1, -1.7)),
        _m("oil_temperature", "機油溫度", "°C", 60.0, 30.0, 0.5, 1.4, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=160,
           bands=(12.0, 21.0, 30.0)),
        _m("vibration_rms", "振動速度", "mm/s", 3.0, 1.3, 0.0, 0.18, 0.2,
           physical_max=60, bands=(3.0, 5.0, 7.5)),
        _m("output_power", "輸出功率", "kW", 2.0, 195.0, 0.0, 2.5, 0.0,
           physical_max=800, direction="low", bands=(-14.0, -26.0, -40.0)),
        _m("fuel_rate", "耗油率", "L/h", 4.0, 52.0, 0.0, 0.9, 0.0,
           physical_max=300, bands=(4.0, 7.0, 11.0)),
    ),
    modes=(
        FailureMode(
            "lubrication_degradation", "潤滑系統劣化",
            {"oil_pressure": -1.6, "oil_temperature": 16.0, "vibration_rms": 2.0},
            symptom="機油壓力下降、油溫上升",
            root_cause="機油稀釋與油泵磨損",
            action="更換機油與濾芯，檢查油泵洩壓閥",
            parts="Engine oil 60L；Oil filter；Fuel filter",
            development_days=(20, 60),
        ),
        FailureMode(
            "cooling_fouling", "冷卻系統阻塞",
            {"coolant_temperature": 18.0, "oil_temperature": 8.0, "fuel_rate": 3.0},
            symptom="冷卻水溫在相同負載下持續偏高",
            root_cause="散熱器積垢、冷卻水劣化",
            action="散熱器清洗、更換冷卻水與節溫器",
            parts="Coolant 40L；Thermostat",
            development_days=(30, 90),
        ),
        FailureMode(
            "injector_fouling", "噴油嘴積碳",
            {"fuel_rate": 7.0, "output_power": -12.0, "vibration_rms": 1.5},
            symptom="耗油率上升、同負載輸出下降且運轉不順",
            root_cause="噴油嘴霧化不良、燃油品質不佳",
            action="噴油嘴清洗校正或更換",
            parts="Injector set",
            development_days=(35, 100),
        ),
    ),
    usage_policy=(
        ("機油與濾芯更換", 250, "lubrication"),
        ("噴油嘴檢查", 1000, "inspection"),
        ("大修", 6000, "overhaul"),
    ),
    time_policy=(
        ("每週無載測試", 7, "inspection"),
        ("年度負載測試", 365, "inspection"),
    ),
)

TRANSFORMER = AssetClass(
    key="power_transformer",
    label="電力變壓器",
    duty="continuous",
    design_life_years=30,
    mtbf_hours=120000,
    specifications=(
        ("rated_power", 2000.0, "kVA"),
        ("primary_voltage", 22800.0, "V"),
        ("secondary_voltage", 380.0, "V"),
        ("rated_current", 3040.0, "A"),
    ),
    measurements=(
        _m("oil_temperature", "油溫", "°C", 22.0, 34.0, 1.0, 1.0, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=140,
           bands=(12.0, 22.0, 32.0)),
        _m("winding_temperature", "繞組溫度", "°C", 26.0, 46.0, 1.0, 1.2, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=200,
           bands=(14.0, 26.0, 38.0)),
        _m("load_current", "負載電流", "A", 5.0, 240.0, 0.0, 3.0, 1.0,
           physical_max=4000, bands=(24.0, 44.0, 62.0)),
        _m("dissolved_gas_h2", "溶解氫氣", "ppm", 22.0, 6.0, 0.0, 2.5, None,
           idle_follows_ambient=False, physical_max=5000,
           bands=(38.0, 78.0, 128.0)),
        _m("moisture_ppm", "油中含水", "ppm", 9.0, 1.0, 0.05, 0.8, None,
           physical_max=100, bands=(5.0, 11.0, 18.0)),
    ),
    modes=(
        FailureMode(
            "insulation_ageing", "絕緣老化",
            {"dissolved_gas_h2": 85.0, "moisture_ppm": 9.0,
             "winding_temperature": 12.0},
            symptom="溶解氫氣與油中含水同步上升",
            root_cause="紙質絕緣熱老化、局部放電",
            action="油品全分析（DGA + 呋喃），評估濾油或改繞",
            parts="Oil treatment；Silica gel",
            development_days=(60, 180),
            severity="critical",
        ),
        FailureMode(
            "cooling_loss", "冷卻能力下降",
            {"oil_temperature": 19.0, "winding_temperature": 22.0},
            symptom="同負載下油溫與繞組溫度同步偏高",
            root_cause="冷卻風扇故障或散熱片積塵",
            action="冷卻風扇檢修、散熱片清洗",
            parts="Cooling fan；Thermostat",
            development_days=(25, 70),
        ),
        FailureMode(
            "oil_degradation", "絕緣油劣化",
            {"moisture_ppm": 14.0, "dissolved_gas_h2": 30.0, "oil_temperature": 6.0},
            symptom="油中含水明顯上升",
            root_cause="呼吸器矽膠失效造成受潮",
            action="更換呼吸器矽膠並真空濾油",
            parts="Silica gel 5kg；Breather",
            development_days=(40, 120),
            severity="minor",
        ),
    ),
    usage_policy=(("油品分析取樣", 8760, "inspection"),),
    time_policy=(
        ("外觀與呼吸器檢查", 90, "inspection"),
        ("年度絕緣試驗", 365, "inspection"),
        ("三年期油品全分析", 1095, "inspection"),
    ),
)

CHILLER = AssetClass(
    key="water_chiller",
    label="冰水主機",
    duty="continuous",
    design_life_years=20,
    mtbf_hours=28000,
    specifications=(
        ("rated_power", 210.0, "kW"),
        ("rated_capacity", 350.0, "RT"),
        ("rated_current", 340.0, "A"),
        ("refrigerant_charge", 180.0, "kg"),
    ),
    measurements=(
        _m("evaporator_pressure", "蒸發壓力", "bar", 3.1, 0.5, 0.0, 0.05, 0.4,
           physical_max=20, direction="low", bands=(-0.35, -0.65, -1.0)),
        _m("condenser_pressure", "冷凝壓力", "bar", 9.2, 2.2, 0.06, 0.12, 1.0,
           physical_max=30, bands=(1.1, 2.0, 3.0)),
        _m("compressor_current", "壓縮機電流", "A", 14.0, 62.0, 0.0, 0.9, 0.5,
           physical_max=500, bands=(6.0, 11.0, 17.0)),
        _m("approach_temperature", "冷凝趨近溫度", "°C", 1.1, 1.3, 0.0, 0.12, 0.0,
           physical_max=25, bands=(1.4, 2.6, 4.0)),
        _m("vibration_rms", "振動速度", "mm/s", 2.1, 0.9, 0.0, 0.13, 0.15,
           physical_max=60, bands=(2.2, 3.9, 6.2)),
    ),
    modes=(
        FailureMode(
            "condenser_fouling", "冷凝器結垢",
            {"approach_temperature": 4.6, "condenser_pressure": 2.4,
             "compressor_current": 9.0},
            symptom="趨近溫度與冷凝壓力同步上升，電流增加",
            root_cause="冷凝器銅管水側結垢，熱傳係數下降",
            action="冷凝器化學清洗並檢討水質處理",
            parts="Cleaning chemical；Gasket set",
            development_days=(45, 130),
        ),
        FailureMode(
            "refrigerant_loss", "冷媒洩漏",
            {"evaporator_pressure": -0.9, "approach_temperature": 2.2,
             "compressor_current": -3.0},
            symptom="蒸發壓力下降但電流不升反降（能力衰退）",
            root_cause="管路接頭或軸封洩漏",
            action="檢漏、補漏並回充冷媒",
            parts="Refrigerant R134a 40kg；Filter drier",
            development_days=(30, 90),
        ),
        FailureMode(
            "bearing_wear", "壓縮機軸承磨損",
            {"vibration_rms": 5.0, "compressor_current": 4.0},
            symptom="振動明顯上升",
            root_cause="壓縮機軸承磨耗",
            action="壓縮機解體檢修",
            parts="Compressor bearing kit",
            development_days=(35, 95),
            severity="critical",
        ),
    ),
    usage_policy=(
        ("冷凝器清洗", 6000, "inspection"),
        ("冷媒與油品檢測", 4000, "inspection"),
        ("壓縮機大修", 25000, "overhaul"),
    ),
    time_policy=(("季節前檢修", 180, "inspection"),),
)

FAN = AssetClass(
    key="centrifugal_fan",
    label="離心風機",
    duty="two_shift",
    design_life_years=18,
    mtbf_hours=30000,
    specifications=(
        ("rated_power", 22.0, "kW"),
        ("rated_flow", 28000.0, "m3/h"),
        ("rated_speed", 980.0, "rpm"),
        ("rated_current", 42.0, "A"),
    ),
    measurements=(
        _m("vibration_rms", "振動速度", "mm/s", 2.0, 1.0, 0.0, 0.12, 0.12,
           physical_max=60, bands=(2.3, 4.0, 6.4)),
        _m("bearing_temperature", "軸承溫度", "°C", 12.0, 18.0, 1.0, 0.8, None,
           idle_follows_ambient=True, physical_min=-20, physical_max=180,
           bands=(10.0, 18.0, 28.0)),
        _m("motor_current", "電流", "A", 5.0, 30.0, 0.0, 0.4, 0.25,
           physical_max=150, bands=(2.6, 4.8, 7.2)),
        _m("rpm", "轉速", "rpm", 986.0, -10.0, 0.0, 2.5, 0.0,
           physical_max=4000, direction="low", bands=(-10.0, -22.0, -38.0)),
        _m("differential_pressure", "風壓差", "bar", 0.6, 1.1, 0.0, 0.05, 0.0,
           physical_max=10, bands=(0.35, 0.65, 1.0)),
    ),
    modes=(
        FailureMode(
            "bearing_wear", "軸承磨損",
            {"vibration_rms": 5.6, "bearing_temperature": 12.0, "motor_current": 2.0},
            symptom="振動與軸承溫度同步上升",
            root_cause="軸承潤滑不足",
            action="更換軸承並加脂",
            parts="Bearing 22216；Grease",
            development_days=(30, 85),
        ),
        FailureMode(
            "filter_clogging", "濾網／風道阻塞",
            {"differential_pressure": 1.4, "motor_current": 2.6, "vibration_rms": 0.6},
            symptom="風壓差與電流上升，振動幾乎不變",
            root_cause="前置濾網積塵、風道積垢",
            action="更換濾網並清洗風道",
            parts="Filter set G4/F7",
            development_days=(15, 45),
            severity="minor",
        ),
        FailureMode(
            "imbalance", "葉輪不平衡",
            {"vibration_rms": 6.2, "bearing_temperature": 5.0, "motor_current": 1.2},
            symptom="振動大幅上升但溫度僅小幅變化",
            root_cause="葉輪積垢或磨損造成質量不平衡",
            action="葉輪清洗並現場動平衡",
            parts="Balance weight set",
            development_days=(20, 55),
        ),
    ),
    usage_policy=(
        ("軸承加脂", 3000, "lubrication"),
        ("濾網更換", 1500, "replacement"),
        ("軸承更換", 15000, "replacement"),
    ),
    time_policy=(("年度風量測試", 365, "inspection"),),
)


CLASSES: dict[str, AssetClass] = {
    klass.key: klass
    for klass in (PUMP, MOTOR, COMPRESSOR, VALVE, GENERATOR, TRANSFORMER, CHILLER, FAN)
}

#  The reference condition the threshold bands are stated against: a healthy
#  asset at 80% load in a 28 °C plant. Written down because a band table with
#  an unstated reference is a band table nobody can re-derive.
REFERENCE_LOAD = 0.80
REFERENCE_AMBIENT = 28.0


def reference_value(klass: AssetClass, measurement: Measurement) -> float:
    """What a healthy asset of this class reads at the reference condition."""
    return (
        measurement.intercept
        + measurement.load_coefficient * REFERENCE_LOAD
        + measurement.ambient_coefficient * REFERENCE_AMBIENT
    )


def threshold_row(klass: AssetClass, measurement: Measurement) -> dict:
    """One row of the condition-threshold table.

    The limits are **offsets from what the reading should be at the operating
    point it was taken at**, not fixed values, and that is the whole design.

    A fixed limit on a load-dependent measurement cannot be made to work. A
    pump running at 45% duty moves 57 m³/h and one at 90% moves 108; any flow
    limit that catches a worn impeller on the second condemns the first
    permanently. The same argument applies to every current, power and
    temperature in the table — which is to say, to almost all of them.

    So the alarm is on the residual: how far the reading is from what the
    response model predicts for this load in this plant room. A healthy asset
    sits near zero at any duty, and a degrading one walks away from it. The
    absolute numbers an operator reads are still produced — the pipeline adds
    the offset back to the expected value — but they are computed per day per
    asset rather than written down once and wrong for half the fleet.

    `reference_*` columns state what the limits come out at under the reference
    condition (80% load, 28 °C), so the table can still be read as a table.
    """
    base = reference_value(klass, measurement)
    warning, critical, emergency = measurement.bands
    return {
        "asset_type": klass.key,
        "asset_type_label": klass.label,
        "parameter": measurement.parameter,
        "parameter_label": measurement.label,
        "unit": measurement.unit,
        "direction": measurement.direction,
        "direction_sign": 1 if measurement.direction == "high" else -1,
        #  Signed, in the measurement's own units: +12 °C above expected, or
        #  -9 m³/h below it. The sign always points the way that is bad.
        "warning_offset": warning,
        "critical_offset": critical,
        "emergency_offset": emergency,
        "reference_value": round(base, 3),
        "reference_load_pct": REFERENCE_LOAD * 100,
        "reference_ambient_c": REFERENCE_AMBIENT,
        "reference_warning": round(base + warning, 3),
        "reference_critical": round(base + critical, 3),
        "reference_emergency": round(base + emergency, 3),
        "physical_min": measurement.physical_min,
        "physical_max": measurement.physical_max,
        "source": "engineering",
        "policy_version": "2026.1",
    }


def response_row(klass: AssetClass, measurement: Measurement) -> dict:
    """One row of the response model: what this measurement *should* read.

        expected = intercept + load_coefficient × load_fraction
                             + ambient_coefficient × ambient

    This is the engineering half of the analysis, and it is a dataset rather
    than code for a reason that shows up immediately in the record: over four
    months a plant warms by several degrees, so every bearing in the fleet
    trends upward and every asset looks like it is degrading. Comparing a
    reading against what the physics says it should be at *this* load in *this*
    plant room removes both effects, and what is left is the equipment.

    In a real installation these coefficients are fitted from a clean period of
    the asset's own history and re-fitted after an overhaul. Here they are the
    design coefficients the simulator used, which is the same table arrived at
    by a different route.
    """
    return {
        "asset_type": klass.key,
        "asset_type_label": klass.label,
        "parameter": measurement.parameter,
        "parameter_label": measurement.label,
        "unit": measurement.unit,
        "intercept": measurement.intercept,
        "load_coefficient": measurement.load_coefficient,
        "ambient_coefficient": measurement.ambient_coefficient,
        "expected_noise": measurement.noise,
        "direction": measurement.direction,
        #  +1 when high is bad, -1 when low is bad. Carried so a rule can be
        #  written once for both instead of twice.
        "direction_sign": 1 if measurement.direction == "high" else -1,
        "source": "engineering",
        "model_version": "2026.1",
    }


# --------------------------------------------------------------------------
# engineering rules
# --------------------------------------------------------------------------
#  Evidence combinations that mean more than any single reading does. Written
#  as data - the condition is an expression the platform's own allow-listed
#  evaluator runs - because the project's rule is that engineering logic must
#  not be compiled into the analysis engine.
#
#  Every column named here is produced by the feature pipeline, and
#  `test_asset_maintenance.py` checks that, so a rule cannot quietly refer to a
#  column that stopped existing.
ENGINEERING_RULES: tuple[dict, ...] = (
    {
        "rule_id": "ER-001",
        "asset_type": "*",
        "name": "軸承劣化徵候",
        "when": (
            "vibration_rms_deviation_pct > 25 and bearing_temperature_deviation_pct > 8 "
            "and load_pct_mean > 40"
        ),
        "finding": "振動與軸承溫度在正常負載下同步偏離基線，符合軸承劣化的典型徵候",
        "failure_mode": "bearing_wear",
        "weight": 34.0,
        "confidence": 0.8,
        "recommended_action": "安排軸承檢查與振動頻譜分析",
        "source": "engineering",
    },
    {
        "rule_id": "ER-002",
        "asset_type": "*",
        "name": "熱應力累積",
        "when": (
            "load_pct_mean > 85 and temperature_headroom_pct < 15 "
            "and runtime_hours_7d > 120"
        ),
        "finding": "長時間高負載運轉且溫度餘裕不足，熱應力持續累積",
        "failure_mode": "thermal_stress",
        "weight": 22.0,
        "confidence": 0.7,
        "recommended_action": "檢討負載分配或加強散熱，避免絕緣與潤滑加速老化",
        "source": "engineering",
    },
    {
        "rule_id": "ER-003",
        "asset_type": "*",
        "name": "上升趨勢尚未越線",
        "when": (
            "worst_trend_slope_per_day > 0.25 and worst_trend_fit > 0.5 "
            "and max_threshold_rank < 2 and worst_limit_progress_pct > 25"
        ),
        "finding": "關鍵量測尚在門檻內，但趨勢穩定上升，門檻無法及時反映",
        "failure_mode": "progressive_degradation",
        "weight": 20.0,
        "confidence": 0.65,
        "recommended_action": "納入趨勢追蹤，於下一個停機窗口安排檢查",
        "source": "engineering",
    },
    {
        "rule_id": "ER-004",
        "asset_type": "*",
        "name": "感測器可疑而非設備劣化",
        "when": "min_quality_score < 55 and max_threshold_rank >= 2",
        "finding": "越線的量測其資料品質不足（卡死、缺漏或超出物理範圍），先確認儀器",
        "failure_mode": "instrument_fault",
        "weight": -30.0,
        "confidence": 0.75,
        "recommended_action": "先校驗或更換感測器，確認後再判定設備狀態",
        "source": "engineering",
    },
    {
        "rule_id": "ER-005",
        "asset_type": "*",
        "name": "保養週期即將到期",
        "when": "interval_usage_pct > 90",
        "finding": "累積運轉時數已達建議保養週期的九成以上",
        "failure_mode": "policy_due",
        "weight": 18.0,
        "confidence": 0.9,
        "recommended_action": "依保養政策安排例行保養",
        "source": "policy",
    },
    {
        "rule_id": "ER-006",
        "asset_type": "*",
        "name": "低負載下的異常",
        "when": "load_pct_mean < 45 and max_threshold_rank >= 2",
        "finding": "在低負載條件下仍出現越線，較不可能是負載造成，指向設備本身",
        "failure_mode": "intrinsic_fault",
        "weight": 16.0,
        "confidence": 0.7,
        "recommended_action": "於低負載條件下複驗，並比對歷史同工況紀錄",
        "source": "engineering",
    },
    {
        "rule_id": "ER-007",
        "asset_type": "centrifugal_pump",
        "name": "水力性能衰退",
        "when": (
            "flow_rate_deviation_pct < -8 and discharge_pressure_deviation_pct < -6 "
            "and power_kw_deviation_pct > 3"
        ),
        "finding": "流量與出口壓力同步下降而功率上升，符合葉輪磨蝕／氣蝕",
        "failure_mode": "impeller_wear",
        "weight": 30.0,
        "confidence": 0.75,
        "recommended_action": "安排葉輪與口環間隙檢查，量測實際揚程曲線",
        "source": "engineering",
    },
    {
        "rule_id": "ER-008",
        "asset_type": "electric_motor",
        "name": "繞組絕緣風險",
        "when": (
            "winding_temperature_deviation_pct > 10 and motor_current_deviation_pct > 6"
        ),
        "finding": "同負載下繞組溫度與電流同步偏高，指向匝間絕緣劣化",
        "failure_mode": "winding_insulation",
        "weight": 32.0,
        "confidence": 0.72,
        "recommended_action": "量測絕緣電阻與極化指數，必要時安排重繞",
        "source": "engineering",
    },
    {
        "rule_id": "ER-009",
        "asset_type": "water_chiller",
        "name": "冷凝側結垢",
        "when": (
            "approach_temperature_deviation_pct > 30 "
            "and condenser_pressure_deviation_pct > 8"
        ),
        "finding": "趨近溫度與冷凝壓力同步上升，熱傳能力下降",
        "failure_mode": "condenser_fouling",
        "weight": 28.0,
        "confidence": 0.78,
        "recommended_action": "安排冷凝器化學清洗並檢討冷卻水質",
        "source": "engineering",
    },
    {
        "rule_id": "ER-010",
        "asset_type": "power_transformer",
        "name": "絕緣老化徵候",
        "when": "dissolved_gas_h2_deviation_pct > 40 and moisture_ppm_deviation_pct > 20",
        "finding": "溶解氫氣與油中含水同步上升，符合絕緣紙熱老化",
        "failure_mode": "insulation_ageing",
        "weight": 36.0,
        "confidence": 0.8,
        "recommended_action": "安排 DGA 與呋喃分析，評估濾油或改繞",
        "source": "engineering",
    },
    {
        "rule_id": "ER-011",
        "asset_type": "air_compressor",
        "name": "潤滑系統劣化",
        "when": (
            "oil_pressure_deviation_pct < -8 and discharge_temperature_deviation_pct > 8"
        ),
        "finding": "油壓下降伴隨排氣溫度上升，潤滑膜不足",
        "failure_mode": "lubrication_degradation",
        "weight": 30.0,
        "confidence": 0.76,
        "recommended_action": "更換潤滑油與油濾，取樣送油品分析",
        "source": "engineering",
    },
    {
        "rule_id": "ER-012",
        "asset_type": "centrifugal_fan",
        "name": "風道阻塞而非機械劣化",
        "when": (
            "differential_pressure_deviation_pct > 25 and motor_current_deviation_pct > 6 "
            "and vibration_rms_deviation_pct < 12"
        ),
        "finding": "風壓差與電流上升而振動幾乎不變，指向濾網或風道阻塞",
        "failure_mode": "filter_clogging",
        "weight": 24.0,
        "confidence": 0.8,
        "recommended_action": "更換濾網並清洗風道，避免誤判為軸承問題",
        "source": "engineering",
    },
    {
        "rule_id": "ER-013",
        "asset_type": "control_valve",
        "name": "閥件卡澀",
        "when": (
            "stroke_time_deviation_pct > 25 "
            "and actuator_air_pressure_deviation_pct > 8"
        ),
        "finding": "行程時間與驅動氣壓需求同步上升，符合填料摩擦增加",
        "failure_mode": "valve_sticking",
        "weight": 26.0,
        "confidence": 0.74,
        "recommended_action": "更換填料、清潔閥桿並重新整定定位器",
        "source": "engineering",
    },
    {
        "rule_id": "ER-014",
        "asset_type": "diesel_generator",
        "name": "冷卻能力不足",
        "when": (
            "coolant_temperature_deviation_pct > 12 and oil_temperature_deviation_pct > 6"
        ),
        "finding": "冷卻水溫與油溫同步偏高，散熱能力下降",
        "failure_mode": "cooling_fouling",
        "weight": 26.0,
        "confidence": 0.72,
        "recommended_action": "清洗散熱器、更換冷卻水與節溫器",
        "source": "engineering",
    },
    {
        "rule_id": "ER-016",
        "asset_type": "*",
        "name": "孤立偏離，缺乏旁證",
        "when": (
            "worst_limit_progress_pct > 55 and second_limit_progress_pct < 20 "
            "and measurement_count >= 3 and signature_match_pct < 40"
        ),
        "finding": (
            "只有單一量測大幅偏離，同一設備其他量測完全沒有跟著動。"
            "真實劣化幾乎不會只影響一個訊號，這比較像感測器本身漂移"
        ),
        "failure_mode": "instrument_fault",
        "weight": -18.0,
        "confidence": 0.6,
        "recommended_action": "以手持儀器複量該點，確認感測器讀值後再判定設備狀態",
        "source": "engineering",
    },
    {
        "rule_id": "ER-015",
        "asset_type": "*",
        "name": "資料不足以判斷",
        "when": "observed_days < 10 or measurement_count < 3",
        "finding": "觀測期間或量測項目不足，任何結論都缺乏依據",
        "failure_mode": "cold_start",
        "weight": -12.0,
        "confidence": 0.9,
        "recommended_action": "沿用製造商建議週期與設計限值，待資料累積後再建立自身基線",
        "source": "engineering",
    },
)
