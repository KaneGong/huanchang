import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, unquote
from cloud.runtime import create_app

class PasswordConfiguration(unittest.TestCase):
    def test_separate_password_round_trips_special_characters(self):
        password="a@:/?#% 空格'"
        base="postgresql://postgres.example@pooler.example:6543/postgres?sslmode=require"
        with patch.dict(os.environ, {"HUANCHANG_DATABASE_URL":base,"HUANCHANG_DATABASE_PASSWORD":password}), patch("cloud.runtime.connect") as connection:
            app=create_app(storage=object(),origin="https://example.test")
            app.extensions["huanchang_runtime"].database()
            actual=urlsplit(connection.call_args.args[0])
        self.assertEqual(unquote(actual.password),password)
        self.assertEqual(actual.hostname,"pooler.example")
        self.assertEqual(actual.port,6543)
        self.assertEqual(actual.query,"sslmode=require")

    def test_existing_full_uri_remains_supported(self):
        base="postgresql://user:existing%40password@pooler.example:6543/postgres?sslmode=require"
        with patch.dict(os.environ, {"HUANCHANG_DATABASE_URL":base,"HUANCHANG_DATABASE_PASSWORD":""}), patch("cloud.runtime.connect") as connection:
            app=create_app(storage=object(),origin="https://example.test")
            app.extensions["huanchang_runtime"].database()
            self.assertEqual(connection.call_args.args[0],base)
