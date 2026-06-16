"""
數據集構建腳本（通過 pipeline 執行）

流程：
  1. 01_data_cleaning: 從原始資料清理
  2. 03_preprocessing: 建立模型可用格式
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_pipiline.stage01_data_cleaning.typhoon.cleaner import clean_and_save
from data_pipiline.stage03_data_preprocessing.typhoon.preprocessor import (
    preprocess_and_save,
)


def main():
    print("=" * 60)
    print("🌀 構建侵臺颱風資料集")
    print("=" * 60)

    # Step 1: 清理
    print("\n📋 Step 1: 資料清理...")
    clean_and_save()

    # Step 2: 前處理
    print("\n📋 Step 2: 資料前處理...")
    summary = preprocess_and_save()

    print(f"\n{'='*60}")
    print(f"📊 資料集摘要")
    print(f"{'='*60}")
    print(f"  總颱風數：{summary['total_typhoons']}")
    print(f"  年份範圍：{summary['year_range'][0]} ~ {summary['year_range'][1]}")
    print(f"  路徑分類分布：")
    for cat, count in sorted(summary["category_distribution"].items()):
        print(f"    類型 {cat}：{count} 筆")
    print("\n✅ 資料集構建完成！")


if __name__ == "__main__":
    main()
