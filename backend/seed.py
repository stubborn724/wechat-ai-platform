"""插入初始数据：租户 + 管理员用户"""
import os
import warnings
from app.database import MysqlSessionLocal
from app.models.mysql_models import Tenant, User, Membership
from app.services.auth_service import hash_password
from app.config import settings

EMAIL = os.getenv("SEED_EMAIL", "admin@wechat.ai")
PASSWORD = os.getenv("SEED_PASSWORD", "admin123")
DISPLAY_NAME = "管理员"

def seed():
    # 检查默认密码（所有环境）
    if PASSWORD in ("admin123", "123456", "password"):
        msg = "SEED_PASSWORD 使用了弱密码 '{}'，请通过环境变量 SEED_PASSWORD 设置强密码".format(PASSWORD)
        if settings.environment == "production":
            print("ERROR: 生产环境禁止使用默认密码，请设置 SEED_PASSWORD 环境变量")
            return
        else:
            warnings.warn(msg)
    db = MysqlSessionLocal()
    try:
        # 检查是否已有数据
        if db.query(User).filter(User.email == EMAIL).first():
            print("管理员用户已存在，跳过")
            return

        # 创建默认租户
        tenant = Tenant(name="默认团队", slug="default")
        db.add(tenant)
        db.flush()

        # 创建管理员用户
        user = User(
            email=EMAIL,
            password_hash=hash_password(PASSWORD),
            display_name=DISPLAY_NAME,
        )
        db.add(user)
        db.flush()

        # 关联用户到租户
        membership = Membership(tenant_id=tenant.id, user_id=user.id, role="admin")
        db.add(membership)

        db.commit()
        print(f"初始化完成！")
        print(f"  邮箱: {EMAIL}")
        print(f"  密码: {PASSWORD}")
    except Exception as e:
        db.rollback()
        print(f"失败: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    seed()
