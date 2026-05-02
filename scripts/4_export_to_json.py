"""
4_export_to_json.py

GEXレベルデータを R2 アップロード用ディレクトリに配置する。
パス構造: data/r2/gex/daily/{date}/{symbol}.json
"""
import os
import sys
import json
import shutil
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DATA_FOLDER = "data"
LEVELS_DIR = os.path.join(DATA_FOLDER, "levels")
R2_OUTPUT = os.path.join(DATA_FOLDER, "r2")


def main():
    logging.info("=" * 60)
    logging.info("EXPORT GEX TO JSON (R2 FORMAT)")
    logging.info("=" * 60)

    if not os.path.exists(LEVELS_DIR):
        logging.error(f"Levels directory not found: {LEVELS_DIR}")
        return False

    json_files = [f for f in os.listdir(LEVELS_DIR) if f.endswith('.json')]
    if not json_files:
        logging.error("No level files found")
        return False

    # 日付の取得（最初のファイルから）
    with open(os.path.join(LEVELS_DIR, json_files[0]), 'r') as f:
        sample = json.load(f)
    date_str = sample.get('date', datetime.now().strftime('%Y-%m-%d'))

    # R2 出力ディレクトリ
    output_dir = os.path.join(R2_OUTPUT, "gex", "daily", date_str)
    os.makedirs(output_dir, exist_ok=True)

    success_count = 0
    total_gex_summary = []

    for json_file in json_files:
        src_path = os.path.join(LEVELS_DIR, json_file)
        dst_path = os.path.join(output_dir, json_file)

        try:
            # ファイルをコピー
            shutil.copy2(src_path, dst_path)
            success_count += 1

            # サマリー情報収集
            with open(src_path, 'r') as f:
                data = json.load(f)
            total_gex_summary.append({
                'ticker': data['ticker'],
                'spotPrice': data['spotPrice'],
                'totalGEX': data['totalGEX'],
                'sentiment': data['sentiment'],
                'hvl': data['levels'].get('hvl'),
                'callWall': data['levels'].get('callWall'),
                'putWall': data['levels'].get('putWall')
            })

        except Exception as e:
            logging.error(f"Error copying {json_file}: {e}")

    # latest.json の生成（メタデータ）
    latest_path = os.path.join(R2_OUTPUT, "gex", "daily", "latest.json")
    latest_info = {
        'lastUpdated': datetime.now().isoformat(),
        'date': date_str,
        'totalSymbols': success_count,
        'summary': total_gex_summary
    }
    with open(latest_path, 'w') as f:
        json.dump(latest_info, f, indent=2)

    logging.info(f"✅ Exported {success_count} files to {output_dir}")
    logging.info(f"✅ Latest info written to {latest_path}")

    return success_count > 0


if __name__ == "__main__":
    if main():
        sys.exit(0)
    else:
        sys.exit(1)
