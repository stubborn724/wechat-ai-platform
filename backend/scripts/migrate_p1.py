"""P1 数据库迁移脚本 — 创建新表 + 补充 comment_leads 字段 + 索引"""
import sys
sys.path.insert(0, '..')

from app.database import mysql_engine, MysqlBase
from sqlalchemy import text, inspect

conn = mysql_engine.connect()
insp = inspect(mysql_engine)


def table_exists(name):
    return insp.has_table(name)


def run():
    print("=== P1 数据库迁移 ===")

    # 1. 创建新表（SQLAlchemy create_all 会自动跳过已存在的）
    MysqlBase.metadata.create_all(mysql_engine)
    print("[OK] create_all 完成")

    # 2. 确认 4 张新表
    for t in ['contact_packages', 'wechat_media_assets', 'contact_deliveries', 'contact_delivery_attempts']:
        assert table_exists(t), f"{t} 未创建"
        print(f"[OK] 表 {t} 已存在")

    # 3. comment_leads 补充资格字段
    cols = [c['name'] for c in insp.get_columns('comment_leads')]
    needed = [
        ('eligibility_status', 'VARCHAR(16)'),
        ('eligibility_reason_code', 'VARCHAR(64)'),
        ('eligibility_reason_text', 'VARCHAR(255)'),
        ('eligibility_recommended_action', 'VARCHAR(64)'),
        ('eligibility_checked_at', 'DATETIME'),
        ('eligibility_expires_at', 'DATETIME'),
        ('eligibility_source', 'VARCHAR(32)'),
    ]
    for name, dtype in needed:
        if name not in cols:
            conn.execute(text(f'ALTER TABLE comment_leads ADD COLUMN {name} {dtype}'))
            print(f"[OK] comment_leads.{name} 已添加")
        else:
            print(f"[--] comment_leads.{name} 已存在")

    # 4. 默认值
    conn.execute(text(
        "UPDATE comment_leads SET eligibility_status = 'unknown' WHERE eligibility_status IS NULL"
    ))
    print(f"[OK] 旧线索 eligibility_status 默认值设为 unknown")

    conn.commit()

    # 5. 验证索引
    for t in ['contact_packages', 'wechat_media_assets', 'contact_deliveries', 'contact_delivery_attempts']:
        idx = [ix['name'] for ix in insp.get_indexes(t)]
        print(f"[OK] {t} 索引: {idx}")

    print("\n=== 迁移完成 ===")


def rollback():
    """回滚脚本"""
    ans = input("确认回滚？将删除 4 张新表并移除 7 个字段 (yes/no): ")
    if ans != 'yes':
        return
    for t in ['contact_delivery_attempts', 'contact_deliveries', 'wechat_media_assets', 'contact_packages']:
        conn.execute(text(f'DROP TABLE IF EXISTS {t}'))
        print(f"[OK] {t} 已删除")
    for col in ['eligibility_status', 'eligibility_reason_code', 'eligibility_reason_text',
                'eligibility_recommended_action', 'eligibility_checked_at',
                'eligibility_expires_at', 'eligibility_source']:
        conn.execute(text(f'ALTER TABLE comment_leads DROP COLUMN IF EXISTS {col}'))
        print(f"[OK] comment_leads.{col} 已移除")
    conn.commit()
    print("=== 回滚完成 ===")


if __name__ == '__main__':
    if '--rollback' in sys.argv:
        rollback()
    else:
        run()
