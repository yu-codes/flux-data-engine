"""
災害映射模組

以「侵臺路徑分類」作為分類標籤
"""

# 氣象署侵臺路徑分類定義
TRACK_CATEGORY_DESCRIPTION = {
    "1": "第1類：通過台灣北部海面向西或西北西進行者",
    "2": "第2類：通過台灣北部向西或西北進行者（含登陸北部）",
    "3": "第3類：通過台灣中部向西進行者（含登陸中部）",
    "4": "第4類：通過台灣南部向西進行者（含登陸南部）",
    "5": "第5類：通過台灣南部海面向西進行者",
    "6": "第6類：沿台灣東岸或東部海面北上者",
    "7": "第7類：通過台灣南部海面向東或東北進行者",
    "8": "第8類：通過台灣南部海面向北或北北西進行者",
    "9": "第9類：西北太平洋或南海生成後對台灣無侵襲，但有影響者（含特殊路徑）",
    "特殊": "特殊路徑",
}


class ImpactMapper:
    """侵臺路徑分類標籤管理"""

    def __init__(self):
        self.descriptions = TRACK_CATEGORY_DESCRIPTION

    def get_description(self, category: str) -> str:
        return self.descriptions.get(str(category), "未知分類")

    def get_all_categories(self) -> list[str]:
        return list(self.descriptions.keys())

    @staticmethod
    def build_label_dict(loader) -> dict[str, str]:
        """從 DataLoader 建立 {typhoon_id: category} 字典"""
        return {r.typhoon_id: r.taiwan_track_category for r in loader.records}
