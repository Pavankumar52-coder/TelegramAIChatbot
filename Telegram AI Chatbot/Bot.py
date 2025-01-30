import os
import time
import logging
import asyncio
import requests
from telethon import TelegramClient, events
from pymongo import MongoClient
from dotenv import load_dotenv
import google.generativeai as genai
from googletrans import Translator

# Load environment variables
load_dotenv()

# Initialize Telegram Bot
api_id = os.getenv("TELEGRAM_API_ID")
api_hash = os.getenv("TELEGRAM_API_HASH")
bot_token = os.getenv("BOT_TOKEN")

bot = TelegramClient("bot_session", api_id, api_hash).start(bot_token=bot_token)

# Initialize MongoDB
client = MongoClient(os.getenv("MONGO_URI"))
db = client["telegram_bot"]
users_collection = db["users"]
chat_collection = db["chat_history"]
files_collection = db["file_analysis"]

# Initialize Gemini API
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Initialize Translator
translator = Translator()

# Logging
logging.basicConfig(level=logging.INFO)


# Step 1: Handle User Registration
@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    chat_id = event.chat_id
    sender = await event.get_sender()
    first_name = sender.first_name
    username = sender.username

    user_exists = users_collection.find_one({"chat_id": chat_id})
    
    if not user_exists:
        users_collection.insert_one(
            {"chat_id": chat_id, "first_name": first_name, "username": username, "phone": None}
        )
        await event.respond("Welcome! Please share your phone number.", buttons=[
            [bot.build_reply_markup([[bot.build_contact_button("Share Phone")]])]
        ])
    else:
        await event.respond("Welcome back! How can I assist you?")


@bot.on(events.NewMessage(func=lambda e: e.contact))
async def save_contact(event):
    chat_id = event.chat_id
    phone_number = event.message.contact.phone_number

    users_collection.update_one({"chat_id": chat_id}, {"$set": {"phone": phone_number}})
    await event.respond(f"Phone number saved successfully! How can I assist you?")


# Step 2: Gemini-Powered Chat with Auto-Translation & Auto Follow-Up
async def follow_up(chat_id):
    """Send a reminder if the user doesn't respond in 5 minutes."""
    await asyncio.sleep(300)  # Wait for 5 minutes
    last_message = chat_collection.find_one({"chat_id": chat_id}, sort=[("timestamp", -1)])
    
    if last_message and last_message.get("bot_response"):  
        await bot.send_message(chat_id, "Are you still there? Let me know if you need further assistance!")


@bot.on(events.NewMessage)
async def gemini_chat(event):
    chat_id = event.chat_id
    user_input = event.text

    # Skip commands
    if user_input.startswith("/"):
        return

    # Detect Language
    detected_lang = await translator.detect(user_input)
    detected_lang = detected_lang.lang
    if detected_lang != "en":
        translated_text = translator.translate(user_input, src=detected_lang, dest="en").text
    else:
        translated_text = user_input

    # Store message in DB
    chat_collection.insert_one({"chat_id": chat_id, "user_input": translated_text, "timestamp": time.time()})

    # Get AI response
    model = genai.GenerativeModel("gemini-pro")
    response = model.generate_content(translated_text)
    bot_response = response.text if response else "Sorry, I couldn't process that."

    # Translate response back to user's language
    if detected_lang != "en":
        bot_response = translator.translate(bot_response, src="en", dest=detected_lang).text

    # Store response in DB
    chat_collection.update_one({"chat_id": chat_id, "user_input": translated_text}, {"$set": {"bot_response": bot_response}})

    await event.respond(bot_response)

    # Start follow-up timer
    asyncio.create_task(follow_up(chat_id))


# Step 3: Image/File Analysis
@bot.on(events.NewMessage)
async def gemini_chat(event):
    if event.photo:  # Check if the message contains an image
        photo = await event.download_media()
        
        if not photo:
            await event.respond("Failed to download image. Please try again.")
            return

        try:
            # Load image properly
            with open(photo, "rb") as image_file:
                image_data = image_file.read()

            # Use Gemini Pro Vision for image descriptions
            model = genai.GenerativeModel("gemini-pro-vision")

            response = model.generate_content([image_data])  # Pass image directly

            # Check if the response is valid
            description = response.text if response and response.text else "Could not generate description."

        except Exception as e:
            description = f"Error processing image: {str(e)}"

        await event.respond(description)

@bot.on(events.NewMessage(func=lambda e: e.text.startswith("/search ")))
async def process_search(event):
    query = event.text.replace("/search ", "")

    # Perform web search
    search_url = f"https://www.googleapis.com/customsearch/v1?q={query}&key={os.getenv('GEMINI_API_KEY')}&cx=your_cse_id"
    response = requests.get(search_url).json()

    results = response.get("items", [])
    if not results:
        await event.respond("No results found.")
        return

    summary = "\n".join([f"{i+1}. {item['title']} - {item['link']}" for i, item in enumerate(results[:5])])
    await event.respond(f"Top search results:\n{summary}")


# Step 5: Translation Feature
@bot.on(events.NewMessage(pattern="/translate (.*)"))
async def translate_text(event):
    text = event.pattern_match.group(1)

    model = genai.GenerativeModel("gemini-pro")
    response = model.generate_content(f"Translate this to English: {text}")

    translation = response.text if response else "Translation unavailable."
    await event.respond(f"Translation: {translation}")


# Run the bot
print("Bot is running...")
bot.run_until_disconnected()