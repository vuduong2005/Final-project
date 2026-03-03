import google.generativeai as genai
import os

# --- Configuration ---
# PASTE YOUR SECRET GOOGLE API KEY HERE
# This script reads the key directly for simplicity
# Make sure this is the *same key* you put in config.py
API_KEY = "AIzaSyAk7Oc74J9_IEYyURz1BQAKiytuVFllWFo" 
# ---------------------

if not API_KEY or API_KEY == "PASTE_YOUR_NEW_API_KEY_HERE":
    print("Error: Please paste your Google API Key into the API_KEY variable in this script.")
    exit()

try:
    genai.configure(api_key=API_KEY)
except Exception as e:
    print(f"Error configuring API: {e}")
    exit()

print("--- Finding Available Gemini Models for 'generateContent' (Vision) ---")

try:
    # List all models
    models = genai.list_models()
    
    found_models = False
    # Iterate and find models that support the method we need
    for model in models:
        # We need the model that supports 'generateContent' (which is what our main app uses)
        if 'generateContent' in model.supported_generation_methods:
            print(f"\nModel Name: {model.name}")
            print(f"  Display Name: {model.display_name}")
            print(f"  Description: {model.description[:70]}...") # Shorten description
            found_models = True

    if not found_models:
        print("\nNo models found that support 'generateContent'.")
        print("This might be an issue with your API key permissions or project setup.")
    else:
        print("\n--- Recommendation ---")
        print("Find the model name above that looks like 'models/gemini-1.5-pro-latest' or 'models/gemini-pro-vision'.")
        print("Copy that exact 'Model Name' (e.g., 'models/gemini-1.5-flash-latest') and paste it into your config.py for the GEMINI_MODEL_NAME variable.")

except Exception as e:
    print(f"\nAn error occurred while trying to list models: {e}")
    print("Please check your API key and network connection.")
