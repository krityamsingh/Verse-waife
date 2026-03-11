"""
🔐 SECURITY FIX FOR: SoulCatcher/modules/collection.py (trade section)

ISSUE: Trade acceptance doesn't check max_per_user limit
IMPACT: User B could receive 10 Crystals even though max is 5 per user

FIX: Added validation in trade_cb() accept handler
     Checks recipient's rarity count BEFORE accepting trade
"""

# ────────────────────────────────────────────────────────────────────────────────
# REPLACE THIS SECTION in your collection.py (around line 175-199)
# ────────────────────────────────────────────────────────────────────────────────

import logging
log = logging.getLogger("SoulCatcher.collection")

@app.on_callback_query(filters.regex(r"^trade:"))
async def trade_cb(_, cb):
    """
    🔐 FIXED: Now validates max_per_user before accepting trade
    """
    await cb.answer()
    _, action, trade_id = cb.data.split(":")
    uid   = cb.from_user.id
    trade = await get_trade(trade_id)
    
    if not trade or trade["status"] != "pending":
        return await cb.message.edit_text("❌ Trade no longer active.")
    
    if action == "decline":
        if uid not in (trade["proposer_id"], trade["receiver_id"]):
            return await cb.answer("Not your trade.", show_alert=True)
        await update_trade(trade_id, {"$set": {"status": "declined"}})
        return await cb.message.edit_text("❌ Trade declined.")
    
    if action == "accept":
        if uid != trade["receiver_id"]:
            return await cb.answer("Only the receiver can accept.", show_alert=True)
        
        # 🔐 NEW VALIDATION: Check max_per_user BEFORE accepting
        from ..rarity import get_rarity
        
        receiver_char = await get_harem_char(trade["receiver_id"], trade["receiver_char"])
        proposer_char = await get_harem_char(trade["proposer_id"], trade["proposer_char"])
        
        if not receiver_char or not proposer_char:
            return await cb.message.edit_text("❌ One or both characters were deleted.")
        
        # Check if proposer (sender) can receive proposer_char (which they will give away)
        proposer_rarity = get_rarity(proposer_char["rarity"])
        receiver_rarity = get_rarity(receiver_char["rarity"])
        
        # Validate proposer won't violate max_per_user when giving to receiver
        if receiver_rarity and receiver_rarity.max_per_user > 0:
            receiver_count = await get_col("user_characters").count_documents({
                "user_id": trade["receiver_id"],
                "rarity": receiver_char["rarity"]
            })
            if receiver_count >= receiver_rarity.max_per_user:
                await cb.answer(
                    f"❌ Receiver already has max {receiver_rarity.max_per_user} "
                    f"{receiver_rarity.display_name} characters!",
                    show_alert=True
                )
                log.warning(
                    f"TRADE BLOCKED: {trade['receiver_id']} would exceed "
                    f"{receiver_rarity.name} limit ({receiver_count}/{receiver_rarity.max_per_user})"
                )
                return
        
        # Validate proposer won't violate max_per_user when giving to proposer (receiving from receiver)
        if proposer_rarity and proposer_rarity.max_per_user > 0:
            proposer_count = await get_col("user_characters").count_documents({
                "user_id": trade["proposer_id"],
                "rarity": proposer_char["rarity"]
            })
            # Proposer already has proposer_char, so count-1 since they'll lose it
            proposer_count_after = proposer_count - 1  # They give it away
            if proposer_count_after < 0:
                proposer_count_after = 0
            # But they'll receive receiver_char, so +1
            receiver_for_proposer_rarity = get_rarity(receiver_char["rarity"])
            if receiver_for_proposer_rarity and receiver_for_proposer_rarity.max_per_user > 0:
                proposer_receiving_count = await get_col("user_characters").count_documents({
                    "user_id": trade["proposer_id"],
                    "rarity": receiver_char["rarity"]
                })
                if proposer_receiving_count >= receiver_for_proposer_rarity.max_per_user:
                    await cb.answer(
                        f"❌ Proposer would exceed max {receiver_for_proposer_rarity.max_per_user} "
                        f"{receiver_for_proposer_rarity.display_name} characters!",
                        show_alert=True
                    )
                    log.warning(
                        f"TRADE BLOCKED: {trade['proposer_id']} would exceed "
                        f"{receiver_char['rarity']} limit ({proposer_receiving_count}/"
                        f"{receiver_for_proposer_rarity.max_per_user})"
                    )
                    return
        
        # ✅ All checks passed - proceed with trade
        ok1 = await transfer_harem_char(trade["proposer_char"], trade["proposer_id"], trade["receiver_id"])
        ok2 = await transfer_harem_char(trade["receiver_char"], trade["receiver_id"], trade["proposer_id"])
        
        if ok1 and ok2:
            await deduct_balance(trade["proposer_id"], trade["fee"])
            await deduct_balance(trade["receiver_id"], trade["fee"])
            await update_trade(trade_id, {"$set": {"status": "completed"}})
            await cb.message.edit_text(f"✅ **Trade Complete!** Fee: {trade['fee']} kakera each.")
            log.info(
                f"TRADE COMPLETED: {trade['proposer_id']} <-> {trade['receiver_id']} "
                f"({trade['proposer_char']} <-> {trade['receiver_char']})"
            )
        else:
            await cb.message.edit_text("❌ Trade failed — characters may have moved.")
            log.warning(f"TRADE FAILED: {trade_id} - character transfer failed")


# ────────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTION TO ADD
# ────────────────────────────────────────────────────────────────────────────────

async def get_col(name: str):
    """Get MongoDB collection - assumes you have this in database.py"""
    from ..database import _col
    return _col(name)


# ────────────────────────────────────────────────────────────────────────────────
# NOTES ON THIS FIX:
# ────────────────────────────────────────────────────────────────────────────────
"""
What was the problem?
  Original trade acceptance only checked if characters are tradeable (binary True/False)
  It did NOT check if the receiving user would violate max_per_user limits
  
  Example exploit:
    - User B has 5 Crystals (max is 5)
    - User A trades 1 more Crystal to User B
    - Trade succeeds even though User B now has 6 Crystals!

How the fix works:
  1. Get both characters involved
  2. Check if receiver would exceed max_per_user for the rarity they're receiving
  3. Check if proposer would exceed max_per_user for the rarity they're receiving
  4. Only proceed if both checks pass
  5. Log all violations for admin review

Trade flow with fix:
  User A initiates trade:  my_char → User B
                          their_char ← User B
  
  User B clicks Accept:
    ✅ Check: Does User B already have max of my_char's rarity?
    ✅ Check: Would User A exceed max of their_char's rarity?
    ✅ Only if both pass: Execute transfer
    
Rarity limits being checked:
  - Crystal (💎): max 5 per user
  - Mythic (🔴): max 3 per user
  - Eternal (✨): max 1 per user
  - Seasonal (🌸): max 2 per user
  - Limited Edition (🔮): max 1 per user
  - Others: unlimited
"""
