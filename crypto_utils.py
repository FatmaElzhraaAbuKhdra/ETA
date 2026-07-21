"""
أدوات تشفير كلمات مرور العملاء باستخدام Fernet (تشفير متماثل).
المفتاح ييجي من متغير البيئة ENCRYPTION_KEY.
لو المفتاح مش موجود: الدوال تشتغل بـ passthrough بدون تشفير للتوافق مع الوضع الحالي.

TODO (عربي): نقل الباسوردات الموجودة فعلاً في قاعدة البيانات للتشفير يحتاج migration script
منفصل يقرأ كل صف، يشفّر الباسورد، ويكتبه تاني في العمود. هذا التعديل لا يفعل ذلك ويبقى
backward-compatible: الباسوردات الـ plaintext ترجع كما هي، والمشفرة (بادئة ENC:) تتفك.
"""
import logging
import os

logger = logging.getLogger(__name__)

_PREFIX = 'ENC:'


def _get_fernet():
    """Return a Fernet instance if ENCRYPTION_KEY is configured, else None."""
    key = os.getenv('ENCRYPTION_KEY', '').strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode())
    except ImportError:
        logger.warning("حزمة cryptography غير مثبتة — الباسوردات محفوظة plaintext")
        return None
    except Exception as e:
        logger.warning(f"ENCRYPTION_KEY غير صالح — الباسوردات محفوظة plaintext: {e}")
        return None


def encrypt_password(plain: str) -> str:
    """تشفير باسورد نصي. يرجع ENC:<base64> أو النص كما هو لو مفيش مفتاح."""
    f = _get_fernet()
    if not f:
        return plain
    return _PREFIX + f.encrypt(plain.encode()).decode()


def decrypt_password(token: str) -> str:
    """فك تشفير باسورد. يدعم الباسوردات الـ plaintext القديمة (backward-compatible)."""
    if not token.startswith(_PREFIX):
        return token  # plaintext قديم — ارجعه كما هو
    f = _get_fernet()
    if not f:
        logger.warning("الباسورد يبدو مشفر لكن ENCRYPTION_KEY غير موجود — سيُرجع كما هو")
        return token
    try:
        return f.decrypt(token[len(_PREFIX):].encode()).decode()
    except Exception as e:
        logger.error(f"فشل فك تشفير الباسورد: {e}")
        return token
