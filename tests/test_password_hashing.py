import unittest

from argon2 import PasswordHasher

from app.core.password_hashing import (
    hash_password,
    password_hash_needs_rehash,
    verify_login_password,
    verify_password_hash,
)


# Flask-AAS previously used Flask-Bcrypt with BCRYPT_HANDLE_LONG_PASSWORDS=True.
# That mode bcrypts the ASCII SHA-256 hexdigest of the submitted UTF-8 password.
LEGACY_PASSWORD = "legacy-correct-password"
LEGACY_FLASK_BCRYPT_HASH = (
    "$2b$12$Xu5MNvTzhYSCFTNiLnky3e08HnFrRrWgt1HyXbGw4GcNaqTUpG77y"
)


class PasswordHashingTests(unittest.TestCase):
    def test_new_password_hashes_use_argon2id(self):
        stored_hash = hash_password("correct horse battery staple")

        self.assertTrue(stored_hash.startswith("$argon2id$"))
        self.assertTrue(
            verify_password_hash(stored_hash, "correct horse battery staple")
        )
        self.assertFalse(verify_password_hash(stored_hash, "wrong password"))
        self.assertFalse(password_hash_needs_rehash(stored_hash))

    def test_argon2id_uses_the_complete_long_password(self):
        password = "a" * 100 + "X"
        changed_tail = "a" * 100 + "Y"
        stored_hash = hash_password(password)

        self.assertTrue(verify_password_hash(stored_hash, password))
        self.assertFalse(verify_password_hash(stored_hash, changed_tail))

    def test_legacy_flask_bcrypt_hash_is_verified_and_marked_for_upgrade(self):
        self.assertTrue(
            verify_password_hash(LEGACY_FLASK_BCRYPT_HASH, LEGACY_PASSWORD)
        )
        self.assertFalse(
            verify_password_hash(LEGACY_FLASK_BCRYPT_HASH, "wrong password")
        )
        self.assertTrue(password_hash_needs_rehash(LEGACY_FLASK_BCRYPT_HASH))

    def test_older_argon2_parameters_are_marked_for_upgrade(self):
        older_hasher = PasswordHasher(
            time_cost=1,
            memory_cost=8_192,
            parallelism=1,
        )
        stored_hash = older_hasher.hash("parameter-upgrade-password")

        self.assertTrue(
            verify_password_hash(stored_hash, "parameter-upgrade-password")
        )
        self.assertTrue(password_hash_needs_rehash(stored_hash))

    def test_login_verifier_accepts_current_and_legacy_hashes(self):
        current_hash = hash_password("current-login-password")

        self.assertTrue(
            verify_login_password(current_hash, "current-login-password")
        )
        self.assertTrue(
            verify_login_password(LEGACY_FLASK_BCRYPT_HASH, LEGACY_PASSWORD)
        )
        self.assertFalse(verify_login_password(None, "wrong-password"))
        self.assertFalse(verify_login_password("not-a-password-hash", "password"))

    def test_unknown_or_malformed_hashes_fail_closed(self):
        self.assertFalse(verify_password_hash("", "password"))
        self.assertFalse(verify_password_hash("not-a-password-hash", "password"))
        self.assertFalse(password_hash_needs_rehash("not-a-password-hash"))


if __name__ == "__main__":
    unittest.main()
