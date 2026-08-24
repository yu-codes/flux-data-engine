"""
降水地區設定 — 集中管理可用的降水地區與其在 overview 資料中的對應欄位

新增地區只需在 RAINFALL_REGIONS 加入一筆即可，全系統（loader / rainfall /
similarity / pipeline / backend）皆透過此處取得地區清單與欄位對應，確保彈性。
"""

# region code -> {field: overview 欄位名, label: 顯示名稱}
RAINFALL_REGIONS: dict[str, dict[str, str]] = {
    "tn": {"field": "event_rain_tn", "label": "臺南"},
    "kh": {"field": "event_rain_kh", "label": "高雄"},
}

DEFAULT_REGION = "tn"


def region_codes() -> list[str]:
    """所有可用地區代碼"""
    return list(RAINFALL_REGIONS.keys())


def region_field(region: str) -> str:
    """取得地區對應的 overview 欄位名"""
    if region not in RAINFALL_REGIONS:
        raise ValueError(
            f"未知降水地區：{region}，可用：{region_codes()}"
        )
    return RAINFALL_REGIONS[region]["field"]


def region_label(region: str) -> str:
    """取得地區顯示名稱"""
    if region not in RAINFALL_REGIONS:
        return region
    return RAINFALL_REGIONS[region]["label"]
