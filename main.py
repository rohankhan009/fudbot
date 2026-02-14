import os
import datetime
import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError

# Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ============= ENVIRONMENT VARIABLES (Railway me set karenge) =============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8427152792:AAHiwGhq3y66osPN8d0Kuj189AJGgxz-aN0")
API_ID = int(os.environ.get("API_ID", 32841523))
API_HASH = os.environ.get("API_HASH", "efdf251d4e233d6ab5d719dae43cd0d3")
PHONE_NUMBER = os.environ.get("PHONE_NUMBER", "+919051252574")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 5004292319))
ORIGINAL_BOT = os.environ.get("ORIGINAL_BOT", "@AppSafe_bot")
# ===========================================================================

# Railway pe port chahiye webhook ke liye
PORT = int(os.environ.get("PORT", 8443))

# Traffic Manager Class (same as before)
class TrafficManager:
    def __init__(self):
        self.request_queue = asyncio.Queue()
        self.pending_replies = {}
        self.user_sessions = {}
        self.client = None
        self.total_processed = 0
        self.total_requests = 0
        self.bot_app = None  # Add this to store bot app reference
    
    async def setup_client(self):
        """Setup Telethon client with your account"""
        self.client = TelegramClient('my_account.session', API_ID, API_HASH)
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            print("\n" + "="*50)
            print("📱 TERA ACCOUNT LOGIN KARNA HOGA!")
            print("⚠️ Railway pe pehli baar run kar raha hai to session save hoga")
            print("="*50)
            
            # Railway pe interactive login nahi ho sakta, isliye session file pehle se ready rakh
            # Ya phir local pe login karke session file upload kar
            try:
                await self.client.send_code_request(PHONE_NUMBER)
                code = input("📲 Code jo Telegram PE AAYA hai wo daal: ")
                
                try:
                    await self.client.sign_in(PHONE_NUMBER, code)
                    print("✅ Login successful!")
                except SessionPasswordNeededError:
                    password = input("🔐 2FA password daal: ")
                    await self.client.sign_in(password=password)
                    print("✅ 2FA verified!")
            except Exception as e:
                print(f"❌ Login error: {e}")
                print("⚠️ Local pe pehle login karke session file Railway pe upload kar!")
        
        print("✅ Tera account login ho gaya!")
        await self.start_reply_listener()
        asyncio.create_task(self.process_queue())
    
    async def start_reply_listener(self):
        """Listen for replies from original bot"""
        @events.register(events.NewMessage(chats=ORIGINAL_BOT))
        async def reply_handler(event):
            if event.message.reply_to_msg_id:
                reply_to_id = event.message.reply_to_msg_id
                
                if reply_to_id in self.pending_replies:
                    user_id, original_file_name = self.pending_replies[reply_to_id]
                    
                    print(f"\n✅ @AppSafe_bot se reply aaya user {user_id} ke liye!")
                    
                    if event.message.document:
                        try:
                            file_path = await event.message.download_media()
                            
                            # Send signed APK to user
                            await self.user_sessions[user_id].send_document(
                                document=open(file_path, 'rb'),
                                filename='signed.apk',
                                caption='✅ signed.apk'
                            )
                            
                            self.total_processed += 1
                            print(f"✅ Signed APK user {user_id} ko bhej diya!")
                            
                            # Notify admin
                            await self.user_sessions[ADMIN_ID].send_message(
                                f"✅ **APK Delivered**\n"
                                f"👤 User: {user_id}\n"
                                f"📁 File: {original_file_name}"
                            )
                            
                            # Cleanup
                            os.remove(file_path)
                            del self.pending_replies[reply_to_id]
                            
                        except Exception as e:
                            print(f"❌ Send failed: {e}")
        
        self.client.add_event_handler(reply_handler)
        print("👂 Reply listener active...")
    
    async def process_queue(self):
        """Process queue for traffic handling"""
        while True:
            try:
                if self.request_queue.empty():
                    await asyncio.sleep(1)
                    continue
                
                user_id, file_path, file_name, bot_context = await self.request_queue.get()
                
                self.total_requests += 1
                print(f"\n📦 Processing user {user_id} (Queue left: {self.request_queue.qsize()})")
                
                # Forward to original bot
                entity = await self.client.get_input_entity(ORIGINAL_BOT)
                msg = await self.client.send_file(
                    entity, 
                    file_path, 
                    caption=f"User: {user_id} | File: {file_name}"
                )
                
                print(f"📤 Forwarded to {ORIGINAL_BOT}. Msg ID: {msg.id}")
                
                # Store for reply tracking
                self.pending_replies[msg.id] = (user_id, file_name)
                self.user_sessions[user_id] = bot_context.bot
                
                # Notify admin
                await bot_context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"📤 **APK Forwarded**\n"
                         f"👤 User: {user_id}\n"
                         f"📁 File: {file_name}"
                )
                
                # Cleanup downloaded file
                os.remove(file_path)
                
                # Rate limit handling
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Queue error: {e}")
                await asyncio.sleep(5)

# Initialize
traffic_manager = TrafficManager()
user_first_seen = {}

# /start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"
    first_name = update.effective_user.first_name or "User"
    
    if user_id not in user_first_seen:
        user_first_seen[user_id] = datetime.datetime.now()
        # Notify admin about new user
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 **New User Started**\n"
                 f"👤 Name: {first_name}\n"
                 f"🆔 ID: {user_id}"
        )
    
    end_date = user_first_seen[user_id] + datetime.timedelta(days=99999)
    days_left = (end_date - datetime.datetime.now()).days
    hours = ((end_date - datetime.datetime.now()).seconds // 3600)
    
    welcome_text = f"""# Apk Protection
bot

Available Tokens: ∞ (processing one apk consumes one token)

Subscription ends in {days_left}.{hours} days

Paid version: Each apk you get can work for 30 days."""

    await update.message.reply_text(welcome_text)

# APK handler
async def handle_apk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_first_seen:
        user_first_seen[user_id] = datetime.datetime.now()
    
    # Validate APK
    if not update.message.document:
        await update.message.reply_text("❌ APK file bhej bhai!")
        return
    
    file_name = update.message.document.file_name
    if not file_name.endswith('.apk'):
        await update.message.reply_text("❌ Sirf APK file bhej!")
        return
    
    file_size_mb = update.message.document.file_size / (1024 * 1024)
    
    # Send immediate responses
    await update.message.reply_text(f"📁 {file_name}\n📊 {file_size_mb:.0f} MB")
    await update.message.reply_text("🔥 FUD Wala")
    await update.message.reply_text(file_name)
    
    # Queue info
    queue_size = traffic_manager.request_queue.qsize()
    await update.message.reply_text(
        f"⏳ Position in queue: #{queue_size + 1}\n"
        f"⚡ Approx wait: {(queue_size + 1) * 30} seconds"
    )
    
    # Notify admin
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📥 **New APK Received**\n"
             f"👤 User: {user_id}\n"
             f"📁 File: {file_name}\n"
             f"📊 Size: {file_size_mb:.0f}MB"
    )
    
    # Download and queue
    try:
        file = await update.message.document.get_file()
        downloaded = await file.download_to_drive()
        
        await traffic_manager.request_queue.put((user_id, downloaded, file_name, context))
        
    except Exception as e:
        logger.error(f"Download error: {e}")
        await update.message.reply_text("❌ File download failed!")

# Admin commands
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    queue_size = traffic_manager.request_queue.qsize()
    pending = len(traffic_manager.pending_replies)
    
    text = f"""👑 **Admin Panel**

📊 **Stats:**
👥 Total Users: {len(user_first_seen)}
🔄 In Queue: {queue_size}
⚙️ Processing: {pending}
✅ Completed: {traffic_manager.total_processed}
📥 Total Requests: {traffic_manager.total_requests}

📱 **Account:** {PHONE_NUMBER}
🤖 **Original Bot:** {ORIGINAL_BOT}
"""
    await update.message.reply_text(text)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    queue_size = traffic_manager.request_queue.qsize()
    pending = len(traffic_manager.pending_replies)
    
    # Calculate active users in last 24h
    now = datetime.datetime.now()
    active_24h = sum(1 for data in user_first_seen.values() 
                    if (now - data).total_seconds() < 86400)
    
    text = f"""📊 **Detailed Stats**

**Users:**
• Total: {len(user_first_seen)}
• Active (24h): {active_24h}

**Queue:**
• Waiting: {queue_size}
• Processing: {pending}
• Completed: {traffic_manager.total_processed}
• Total Req: {traffic_manager.total_requests}

**Performance:**
• Avg wait: {queue_size * 30}s
"""
    await update.message.reply_text(text)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Admin only!")
        return
    
    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    
    sent = 0
    failed = 0
    
    status_msg = await update.message.reply_text(f"📢 Broadcasting to {len(user_first_seen)} users...")
    
    for uid in user_first_seen.keys():
        try:
            await context.bot.send_message(
                chat_id=uid, 
                text=f"📢 **Broadcast Message**\n\n{message}"
            )
            sent += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await status_msg.edit_text(f"✅ Sent to {sent} users\n❌ Failed: {failed}")

# Railway ke liye health check endpoint (webhook mode)
async def health_check(request):
    return "OK"

# Error handler
async def error(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❌ **Error**\n{context.error}"
        )
    except:
        pass

# Main function
async def main():
    print("\n" + "="*60)
    print("🔥 PAPI ATMA PROXY BOT 🔥".center(60))
    print("="*60 + "\n")
    
    print(f"📱 Logging into account: {PHONE_NUMBER}")
    
    # Setup traffic manager
    await traffic_manager.setup_client()
    
    # Bot setup
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Store app reference in traffic manager
    traffic_manager.bot_app = app
    
    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_apk))
    app.add_error_handler(error)
    
    print("\n" + "="*60)
    print("✅ BOT IS RUNNING!".center(60))
    print(f"🤖 Original bot: {ORIGINAL_BOT}".center(60))
    print(f"👑 Admin ID: {ADMIN_ID}".center(60))
    print("="*60 + "\n")
    
    # Notify admin that bot started
    await app.bot.send_message(
        chat_id=ADMIN_ID,
        text="🚀 **Bot Started on Railway!**\n\n✅ Ready to process APKs"
    )
    
    # Railway pe webhook mode me chalana hai
    # Start webhook
    await app.initialize()
    await app.start()
    
    # Railway automatically provides PORT env variable
    await app.updater.start_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"https://{os.environ.get('RAILWAY_STATIC_URL', '')}/{BOT_TOKEN}"
    )
    
    # Keep running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())