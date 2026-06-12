import uuid
from datetime import UTC, datetime

from quart.sessions import SessionInterface, SessionMixin


class MongoSession(dict, SessionMixin):
    def __init__(self, initial=None, sid=None):
        super().__init__(initial or {})
        self.sid = sid
        self.accessed = False
        self.modified = False

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.accessed = True
        self.modified = True

    def __delitem__(self, key):
        super().__delitem__(key)
        self.accessed = True
        self.modified = True

    def clear(self):
        super().clear()
        self.accessed = True
        self.modified = True

    def pop(self, key, *args):
        self.accessed = True
        self.modified = True
        return super().pop(key, *args)

    def popitem(self):
        self.accessed = True
        self.modified = True
        return super().popitem()

    def update(self, *args, **kwargs):
        self.accessed = True
        self.modified = True
        super().update(*args, **kwargs)

    def setdefault(self, key, default=None):
        self.accessed = True
        self.modified = True
        return super().setdefault(key, default)


class MongoSessionInterface(SessionInterface):
    def __init__(self, collection_name="sessions"):
        self.collection_name = collection_name

    def open_session(self, app, request):
        from app.services.db import db

        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
        sid = request.cookies.get(cookie_name)
        if not sid:
            return MongoSession(sid=str(uuid.uuid4()))

        try:
            doc = db.get_collection(self.collection_name).find_one({"sid": sid})
            if doc:
                # Check expiration
                if doc.get("expiry") and datetime.now(UTC).replace(tzinfo=None) > doc["expiry"]:
                    db.get_collection(self.collection_name).delete_one({"sid": sid})
                    return MongoSession(sid=str(uuid.uuid4()))
                return MongoSession(doc.get("data", {}), sid=sid)
        except Exception:
            pass

        return MongoSession(sid=sid)

    def save_session(self, app, session, response):
        from app.services.db import db

        domain = self.get_cookie_domain(app)
        path = self.get_cookie_path(app)
        cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")

        if not session:
            if session.modified:
                response.delete_cookie(cookie_name, domain=domain, path=path)
                try:
                    db.get_collection(self.collection_name).delete_one({"sid": session.sid})
                except Exception:
                    pass
            return

        # If not modified and not accessed, no need to save
        if not session.accessed and not session.modified:
            return

        expiry = datetime.now(UTC).replace(tzinfo=None) + app.permanent_session_lifetime

        try:
            db.get_collection(self.collection_name).update_one(
                {"sid": session.sid}, {"$set": {"data": dict(session), "expiry": expiry}}, upsert=True
            )
        except Exception:
            pass

        response.set_cookie(
            cookie_name,
            session.sid,
            expires=expiry,
            httponly=self.get_cookie_httponly(app),
            domain=domain,
            path=path,
            secure=self.get_cookie_secure(app),
            samesite=self.get_cookie_samesite(app),
        )
