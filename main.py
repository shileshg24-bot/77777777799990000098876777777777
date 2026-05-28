import os
import re
import asyncio
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from pyrogram.errors import SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream
from pytgcalls.exceptions import NoActiveGroupCall

BOT_TOKEN = "8961167961:AAGHNNeisxhlu7WtgWE6sPUV1RL4kcv28H4"
API_ID = 30898631
API_HASH = "63103daf2f1e96ac7e9826c08eff110a"

# Global storage
user_clients = {}           # phone -> client
user_phones_list = {}       # user_id -> list of phones
user_states = {}            # For login flow
pytgcalls_clients = {}      # phone -> pytgcalls client
vc_chat_ids = {}            # phone -> chat_id
vc_active = {}              # phone -> bool
login_queue = {}            # For multi-number login

bot = Client("bot_controller", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

def clean_otp(text):
    return re.sub(r'\s+', '', text).strip()

async def get_system_info():
    cpu = psutil.cpu_percent()
    ram = psutil.virtual_memory().percent
    return f"🖥️ CPU: {cpu}% | RAM: {ram}%"

# ==================== COMMANDS MENU (ALWAYS VISIBLE) ====================
async def setup_bot_commands():
    commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("login", "Login multiple numbers"),
        BotCommand("mynumbers", "Show all login numbers"),
        BotCommand("joinvc", "Join voice chat"),
        BotCommand("logoutvc", "Logout all from VC"),
        BotCommand("help", "Show help"),
        BotCommand("cancel", "Cancel operation")
    ]
    await bot.set_bot_commands(commands)
    print("✅ Commands menu configured")

# ==================== MAIN KEYBOARD ====================
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📱 LOGIN NUMBERS", callback_data="login_numbers")],
        [InlineKeyboardButton("📋 MY NUMBERS", callback_data="my_numbers")],
        [InlineKeyboardButton("🎙️ JOIN VC", callback_data="join_vc")],
        [InlineKeyboardButton("🚪 LOGOUT ALL VC", callback_data="logout_all_vc")],
        [InlineKeyboardButton("❓ HELP", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== HELPERS ====================
def extract_phones_from_text(text: str):
    phones = re.findall(r'\+?\d{10,15}', text)
    cleaned = []
    for p in phones:
        if not p.startswith('+'):
            p = '+' + p
        if p not in cleaned:
            cleaned.append(p)
    return cleaned

async def resolve_chat_identifier(client: Client, identifier: str):
    try:
        identifier = identifier.strip()
        if "t.me/" in identifier:
            username = identifier.split('t.me/')[-1].split('?')[0].replace('+', '')
            chat = await client.get_chat(username)
            return chat.id
        if identifier.startswith('@'):
            chat = await client.get_chat(identifier)
            return chat.id
        if identifier.startswith('-100') or identifier.isdigit() or (identifier.startswith('-') and identifier[1:].isdigit()):
            chat_id = int(identifier)
            chat = await client.get_chat(chat_id)
            return chat.id
        chat = await client.get_chat(identifier)
        return chat.id
    except Exception as e:
        print(f"Resolve error: {e}")
        return None

# ==================== COMMAND HANDLERS ====================
@bot.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    sys_info = await get_system_info()
    await message.reply_text(
        f"🤖 **VC BOT CONTROLLER**\n\n"
        f"📌 **Commands (always visible):**\n"
        f"• /login - Login multiple numbers\n"
        f"• /mynumbers - Show all numbers\n"
        f"• /joinvc - Join voice chat\n"
        f"• /logoutvc - Logout all from VC\n\n"
        f"**System:** {sys_info}",
        reply_markup=get_main_keyboard()
    )

@bot.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    help_text = """
📚 **HELP GUIDE**

**1. /login** - Login multiple numbers
   Send: `+919876543210 +919876543211`

**2. /mynumbers** - Show all login numbers

**3. /joinvc** - Join voice chat
   Send channel/group link or ID

**4. /logoutvc** - Logout all from VC

**5. /cancel** - Cancel operation
"""
    await message.reply_text(help_text, reply_markup=get_main_keyboard())

@bot.on_message(filters.command("login"))
async def login_command(client: Client, message: Message):
    user_id = message.from_user.id
    await message.reply_text(
        "📱 **LOGIN NUMBERS**\n\n"
        "Send phone numbers (space separated):\n"
        "Example: `+919876543210 +919876543211`\n\n"
        "Type /cancel to abort"
    )
    user_states[user_id] = {'step': 'waiting_numbers'}

@bot.on_message(filters.command("mynumbers"))
async def mynumbers_command(client: Client, message: Message):
    user_id = message.from_user.id
    phones = user_phones_list.get(user_id, [])
    
    if not phones:
        await message.reply_text("❌ No numbers logged in!\n\nUse /login to add numbers.", reply_markup=get_main_keyboard())
        return
    
    status_text = "📋 **YOUR NUMBERS**\n\n"
    keyboard = []
    
    for phone in phones:
        client_obj = user_clients.get(phone)
        is_active = client_obj and client_obj.is_connected
        
        if is_active:
            try:
                me = await client_obj.get_me()
                status = "🟢 Active"
                name = me.first_name or "User"
            except:
                status = "🔴 Expired"
                name = "Unknown"
                if phone in user_clients:
                    del user_clients[phone]
        else:
            status = "🔴 Expired"
            name = "Unknown"
        
        status_text += f"📞 `{phone}`\n   👤 {name}\n   📊 {status}\n\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Delete {phone}", callback_data=f"delete_{phone}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back")])
    
    await message.reply_text(status_text, reply_markup=InlineKeyboardMarkup(keyboard))

@bot.on_message(filters.command("joinvc"))
async def joinvc_command(client: Client, message: Message):
    user_id = message.from_user.id
    phones = user_phones_list.get(user_id, [])
    
    if not phones:
        await message.reply_text("❌ No numbers logged in!\n\nUse /login first.", reply_markup=get_main_keyboard())
        return
    
    active_phones = []
    for phone in phones:
        if phone in user_clients and user_clients[phone].is_connected:
            active_phones.append(phone)
    
    if not active_phones:
        await message.reply_text("❌ No active sessions!\n\nUse /login again.", reply_markup=get_main_keyboard())
        return
    
    await message.reply_text(
        "🎙️ **JOIN VOICE CHAT**\n\n"
        f"📱 Active numbers: {len(active_phones)}\n\n"
        "Send channel/group link or ID:\n"
        "Example: `https://t.me/username` or `-1001234567890`"
    )
    user_states[user_id] = {'step': 'waiting_vc_target', 'phones': active_phones}

@bot.on_message(filters.command("logoutvc"))
async def logoutvc_command(client: Client, message: Message):
    user_id = message.from_user.id
    phones = user_phones_list.get(user_id, [])
    
    if not phones:
        await message.reply_text("❌ No numbers logged in!", reply_markup=get_main_keyboard())
        return
    
    status_msg = await message.reply_text("🚪 Logging out all numbers from VC...")
    
    success_count = 0
    for phone in phones:
        if phone in pytgcalls_clients:
            try:
                await pytgcalls_clients[phone].leave_call(vc_chat_ids.get(phone, 0))
                await pytgcalls_clients[phone].stop()
                del pytgcalls_clients[phone]
                success_count += 1
            except:
                pass
        vc_active[phone] = False
    
    await status_msg.edit_text(f"✅ Logged out {success_count} numbers from VC!", reply_markup=get_main_keyboard())

@bot.on_message(filters.command("cancel"))
async def cancel_command(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in user_states:
        del user_states[user_id]
    if user_id in login_queue:
        del login_queue[user_id]
    await message.reply_text("❌ Operation cancelled!", reply_markup=get_main_keyboard())

# ==================== MULTI-NUMBER LOGIN ====================
async def process_login_queue(user_id: int):
    queue = login_queue.get(user_id, [])
    if not queue:
        return
    
    numbers = queue.copy()
    login_queue[user_id] = []
    
    for phone in numbers:
        await bot.send_message(user_id, f"📱 Logging in: `{phone}`")
        
        try:
            session_name = f"sessions/user_{user_id}_{phone.replace('+', '')}"
            client_obj = Client(session_name, api_id=API_ID, api_hash=API_HASH)
            await client_obj.connect()
            
            code_hash = await client_obj.send_code(phone)
            
            user_states[f"{user_id}_{phone}"] = {
                'step': 'otp',
                'hash': code_hash.phone_code_hash,
                'phone': phone,
                'client': client_obj,
                'attempts': 0
            }
            
            await bot.send_message(
                user_id, 
                f"🔑 OTP sent for `{phone}`\nReply with OTP (5 digits)\nType `skip` to skip\nType `cancel` to stop"
            )
            return
            
        except Exception as e:
            await bot.send_message(user_id, f"❌ Failed for `{phone}`: {str(e)[:50]}\nMoving to next...")
            continue
    
    await bot.send_message(user_id, "✅ Login process completed! Use /mynumbers to see all numbers.", reply_markup=get_main_keyboard())

# ==================== VC JOIN ====================
async def join_single_number_to_vc(phone: str, chat_id: int, user_id: int, index: int, total: int):
    """Join a single number to VC"""
    try:
        client_obj = user_clients.get(phone)
        if not client_obj or not client_obj.is_connected:
            await bot.send_message(user_id, f"❌ [{index}/{total}] `{phone}`: Session expired")
            return False
        
        pytgcalls_client = PyTgCalls(client_obj)
        await pytgcalls_client.start()
        
        await pytgcalls_client.join_group_call(
            chat_id,
            MediaStream(MediaStream.EMPTY)
        )
        
        pytgcalls_clients[phone] = pytgcalls_client
        vc_chat_ids[phone] = chat_id
        vc_active[phone] = True
        
        await bot.send_message(user_id, f"✅ [{index}/{total}] `{phone}`: Joined VC!")
        return True
        
    except NoActiveGroupCall:
        await bot.send_message(user_id, f"❌ [{index}/{total}] `{phone}`: No active VC")
        return False
    except Exception as e:
        await bot.send_message(user_id, f"❌ [{index}/{total}] `{phone}`: {str(e)[:50]}")
        return False

# ==================== CALLBACK HANDLERS ====================
@bot.on_callback_query()
async def callback_handler(client: Client, callback_query):
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    if data == "login_numbers":
        await callback_query.message.delete()
        await login_command(client, callback_query.message)
    elif data == "my_numbers":
        await callback_query.message.delete()
        await mynumbers_command(client, callback_query.message)
    elif data == "join_vc":
        await callback_query.message.delete()
        await joinvc_command(client, callback_query.message)
    elif data == "logout_all_vc":
        await callback_query.message.delete()
        await logoutvc_command(client, callback_query.message)
    elif data == "help":
        await callback_query.message.delete()
        await help_handler(client, callback_query.message)
    elif data == "back":
        await callback_query.message.delete()
        await start_handler(client, callback_query.message)
    elif data.startswith("delete_"):
        phone = data.replace("delete_", "")
        phones = user_phones_list.get(user_id, [])
        
        if phone in phones:
            phones.remove(phone)
            user_phones_list[user_id] = phones
            
            if phone in user_clients:
                try:
                    await user_clients[phone].disconnect()
                except:
                    pass
                del user_clients[phone]
            
            await callback_query.answer(f"Deleted: {phone}")
            await mynumbers_command(client, callback_query.message)
    
    await callback_query.answer()

# ==================== MESSAGE HANDLER ====================
@bot.on_message(filters.text & filters.private & ~filters.command)
async def handle_messages(client: Client, message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Handle multi-number login
    if user_id in user_states and user_states[user_id].get('step') == 'waiting_numbers':
        phones = extract_phones_from_text(text)
        
        if not phones:
            await message.reply_text("❌ No valid phone numbers found!\nSend numbers like: `+919876543210 +919876543211`")
            return
        
        login_queue[user_id] = phones
        if user_id not in user_phones_list:
            user_phones_list[user_id] = []
        
        await message.reply_text(f"✅ Found {len(phones)} numbers!\nStarting login process...")
        del user_states[user_id]
        await process_login_queue(user_id)
        return
    
    # Handle VC target
    if user_id in user_states and user_states[user_id].get('step') == 'waiting_vc_target':
        target = text
        phones = user_states[user_id].get('phones', [])
        del user_states[user_id]
        
        status_msg = await message.reply_text(f"🔍 Resolving chat...")
        
        # Get first client to resolve chat
        first_phone = None
        first_client = None
        for phone in phones:
            if phone in user_clients and user_clients[phone].is_connected:
                first_phone = phone
                first_client = user_clients[phone]
                break
        
        if not first_client:
            await status_msg.edit_text("❌ No active session found!")
            return
        
        chat_id = await resolve_chat_identifier(first_client, target)
        
        if not chat_id:
            await status_msg.edit_text("❌ Could not resolve chat!\nMake sure the link/ID is correct.")
            return
        
        await status_msg.edit_text(f"✅ Chat resolved!\n🎤 Joining {len(phones)} numbers to VC...\n⏱️ 2 sec delay")
        
        success_count = 0
        for i, phone in enumerate(phones, 1):
            result = await join_single_number_to_vc(phone, chat_id, user_id, i, len(phones))
            if result:
                success_count += 1
            await asyncio.sleep(2)
        
        await status_msg.edit_text(f"✅ Joined: {success_count}/{len(phones)} numbers to VC!", reply_markup=get_main_keyboard())
        return
    
    # OTP handling
    for key in list(user_states.keys()):
        if isinstance(key, str) and key.startswith(f"{user_id}_"):
            phone = key.replace(f"{user_id}_", "")
            state = user_states[key]
            
            if state['step'] == 'otp':
                if text.lower() == 'skip':
                    await message.reply_text(f"⏭️ Skipped: `{phone}`")
                    del user_states[key]
                    await process_login_queue(user_id)
                    return
                
                if text.lower() == 'cancel':
                    await message.reply_text("❌ Cancelled all logins!")
                    if user_id in login_queue:
                        del login_queue[user_id]
                    del user_states[key]
                    return
                
                otp = clean_otp(text)
                if not otp.isdigit() or len(otp) != 5:
                    await message.reply_text(f"❌ Invalid OTP! Send 5-digit code or 'skip'\nExample: `12345`")
                    return
                
                state['attempts'] = state.get('attempts', 0) + 1
                
                try:
                    client_obj = state['client']
                    await client_obj.sign_in(
                        phone_number=state['phone'],
                        phone_code_hash=state['hash'],
                        phone_code=otp
                    )
                    
                    me = await client_obj.get_me()
                    user_clients[phone] = client_obj
                    
                    if user_id not in user_phones_list:
                        user_phones_list[user_id] = []
                    if phone not in user_phones_list[user_id]:
                        user_phones_list[user_id].append(phone)
                    
                    await message.reply_text(f"✅ Logged in: `{phone}`\n👤 {me.first_name}")
                    del user_states[key]
                    await process_login_queue(user_id)
                    
                except SessionPasswordNeeded:
                    state['step'] = 'password'
                    await message.reply_text(f"🔒 2FA for `{phone}`\nSend your password:")
                    
                except Exception as e:
                    error = str(e)
                    remaining = 3 - state['attempts']
                    if remaining <= 0:
                        await message.reply_text(f"❌ Too many failed attempts for `{phone}`!\nSkipping...")
                        del user_states[key]
                        await process_login_queue(user_id)
                    else:
                        await message.reply_text(f"❌ Wrong OTP! {remaining} attempts left. Try again or 'skip'")
                
                return
            
            elif state['step'] == 'password':
                if text.lower() == 'skip':
                    await message.reply_text(f"⏭️ Skipped (2FA): `{phone}`")
                    del user_states[key]
                    await process_login_queue(user_id)
                    return
                
                state['attempts'] = state.get('attempts', 0) + 1
                
                try:
                    client_obj = state['client']
                    await client_obj.check_password(password=text)
                    
                    me = await client_obj.get_me()
                    user_clients[phone] = client_obj
                    
                    if user_id not in user_phones_list:
                        user_phones_list[user_id] = []
                    if phone not in user_phones_list[user_id]:
                        user_phones_list[user_id].append(phone)
                    
                    await message.reply_text(f"✅ Logged in: `{phone}`\n👤 {me.first_name}")
                    del user_states[key]
                    await process_login_queue(user_id)
                    
                except Exception as e:
                    remaining = 3 - state['attempts']
                    if remaining <= 0:
                        await message.reply_text(f"❌ Too many failed attempts for `{phone}`!\nSkipping...")
                        del user_states[key]
                        await process_login_queue(user_id)
                    else:
                        await message.reply_text(f"❌ Wrong password! {remaining} attempts left. Try again or 'skip'")
                
                return

# ==================== MAIN ====================
async def main():
    print("\n" + "="*60)
    print("🤖 VC BOT CONTROLLER - MULTI NUMBER")
    print("="*60)
    print("✅ Features:")
    print("   • Multi-number login")
    print("   • All numbers join VC (2 sec delay)")
    print("   • Commands always visible")
    print("="*60)
    
    # Create sessions directory
    os.makedirs("sessions", exist_ok=True)
    
    await bot.start()
    await setup_bot_commands()
    
    print("\n✅ Bot is running!")
    print("📱 Send /start on Telegram")
    
    # Keep bot running
    await asyncio.Event().wait()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n❌ Bot stopped!")
    except Exception as e:
        print(f"\n❌ Error: {e}")