"""MongoDB Client and Helper Services for DealerDesk.

Provides real-time audit logging, AI assistant conversation memory,
supplier sync logs, and document mirroring for MongoDB with smart availability caching.
"""
import os
import time
import logging
from datetime import datetime, timezone
from decimal import Decimal
import pymongo
from pymongo.errors import PyMongoError

logger = logging.getLogger("dealerdesk.mongodb")

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "dealerdesk_mongo")

_client = None
_last_check_time = 0.0
_is_available = None


def is_mongodb_available() -> bool:
    global _client, _last_check_time, _is_available
    now = time.time()
    # Cache availability status for 15 seconds to avoid connection stalls
    if _is_available is not None and (now - _last_check_time) < 15.0:
        return _is_available

    _last_check_time = now
    try:
        if _client is None:
            _client = pymongo.MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=800,
                connectTimeoutMS=800,
                socketTimeoutMS=1000,
            )
        _client.admin.command("ping")
        _is_available = True
    except Exception:
        _is_available = False

    return _is_available


def get_mongo_db():
    if not is_mongodb_available():
        return None
    try:
        return _client[MONGODB_DB_NAME]
    except Exception:
        return None


def _serialize_doc(val):
    if isinstance(val, Decimal):
        return str(val)
    elif isinstance(val, datetime):
        return val.isoformat()
    elif isinstance(val, dict):
        return {k: _serialize_doc(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_serialize_doc(x) for x in val]
    return val


def mirror_product(product_data: dict):
    db = get_mongo_db()
    if db is None:
        return
    try:
        doc = _serialize_doc(dict(product_data))
        doc["synced_at"] = datetime.now(timezone.utc).isoformat()
        db.products.update_one({"sku": doc["sku"]}, {"$set": doc}, upsert=True)
    except Exception as e:
        logger.debug(f"MongoDB mirror_product failed: {e}")


def mirror_dealer(dealer_data: dict):
    db = get_mongo_db()
    if db is None:
        return
    try:
        doc = _serialize_doc(dict(dealer_data))
        doc["synced_at"] = datetime.now(timezone.utc).isoformat()
        db.dealers.update_one({"dealer_code": doc["dealer_code"]}, {"$set": doc}, upsert=True)
    except Exception as e:
        logger.debug(f"MongoDB mirror_dealer failed: {e}")


def mirror_order(order_data: dict):
    db = get_mongo_db()
    if db is None:
        return
    try:
        doc = _serialize_doc(dict(order_data))
        doc["synced_at"] = datetime.now(timezone.utc).isoformat()
        db.orders.update_one({"order_number": doc["order_number"]}, {"$set": doc}, upsert=True)
        db.order_events.insert_one({
            "order_number": doc["order_number"],
            "status": doc.get("status"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "grand_total": doc.get("grand_total"),
            "event_type": "ORDER_UPDATE",
        })
    except Exception as e:
        logger.debug(f"MongoDB mirror_order failed: {e}")


def log_audit_event(event_type: str, details: dict):
    db = get_mongo_db()
    if db is None:
        return
    try:
        db.audit_logs.insert_one({
            "event_type": event_type,
            "details": _serialize_doc(details),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.debug(f"MongoDB log_audit_event failed: {e}")


def log_supplier_sync(sync_result: dict):
    db = get_mongo_db()
    if db is None:
        return
    try:
        db.supplier_sync_logs.insert_one({
            "result": _serialize_doc(sync_result),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.debug(f"MongoDB log_supplier_sync failed: {e}")


def save_chat_turn(session_id: str, dealer_id: int, user_message: str, reply: str, cited_skus: list, tool_calls: list, actions: list):
    db = get_mongo_db()
    if db is None:
        return
    try:
        turn = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_message": user_message,
            "reply": reply,
            "cited_skus": cited_skus,
            "tool_calls": tool_calls,
            "actions": actions,
        }
        db.assistant_sessions.update_one(
            {"session_id": session_id},
            {
                "$set": {"dealer_id": dealer_id, "updated_at": datetime.now(timezone.utc).isoformat()},
                "$push": {"turns": turn},
            },
            upsert=True,
        )
    except Exception as e:
        logger.debug(f"MongoDB save_chat_turn failed: {e}")


def get_chat_history(session_id: str) -> list:
    db = get_mongo_db()
    if db is None:
        return []
    try:
        doc = db.assistant_sessions.find_one({"session_id": session_id})
        if doc and "turns" in doc:
            return doc["turns"]
    except Exception as e:
        logger.debug(f"MongoDB get_chat_history failed: {e}")
    return []


def get_mongo_stats() -> dict:
    available = is_mongodb_available()
    if not available:
        return {"connected": False, "uri": MONGODB_URI, "database": MONGODB_DB_NAME, "collections": {}}
    try:
        db = get_mongo_db()
        coll_names = db.list_collection_names()
        counts = {}
        for c in coll_names:
            counts[c] = db[c].count_documents({})
        return {
            "connected": True,
            "uri": MONGODB_URI,
            "database": MONGODB_DB_NAME,
            "collections": counts,
        }
    except Exception as e:
        return {"connected": False, "error": str(e)}
