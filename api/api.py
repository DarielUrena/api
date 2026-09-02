import json
import os
import re
import time
import httpx
from flask import Flask, redirect, render_template, render_template_string, request
import requests


from config import (
    anilist_clientid,
    anilist_client_secret,
    twitch_clientid,
    twitch_client_secret,
)

app = Flask(__name__) #For flask

anilist_redirect_url = "http://127.0.0.1:8000/callback"

port = 8000

ani_access_token = None
ani_refresh_token = None
ani_token_expires_at = 0

twitch_access_token = None
twitch_expire_at = 0

def strip_htmltags(text): #cleans html tags from strings
    if not text:
        return ""
    return re.sub(r"<[^>]*>?", "", str(text)) #if there are HTML tags, replace them with an empty string.

def get_twitch_token():
    global twitch_access_token, twitch_expire_at #modifies the global variable of twitch_access_token and twitch_expire_at
    now = int(time.time()) #now variable is set to the current time

    if twitch_access_token and now < twitch_expire_at: #checks if we already have a token saved and that it has not expired yet.
        return twitch_access_token, None #If true, return the token and None for errors

    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": twitch_clientid,
        "client_secret": twitch_client_secret,
        "grant_type": 'client_credentials', #used to access data from the server
    }

    try:
        response = requests.post(url, data=payload) #sends a POST request
        json_data = response.json()
        if "access_token" not in json_data:
            return None, 'No access token in Twitch response'

        twitch_access_token = json_data["access_token"] #globally saves the token
        twitch_expire_at = now + json_data.get("expires_in", 3600) #sets the expiration time
        return twitch_access_token, None
    except Exception as e:
        return None, f"Twitch token request error: {e}" #returns token, otherwise return error message if failed

def query_twitch_channels(search, token): #searches for twitch channels based on anime name
    url = f"https://api.twitch.tv/helix/search/channels?query={search}" #build url based on twitch api reference
    headers = {
        "Client-ID": twitch_clientid,
        "Authorization": f"Bearer {token}",
        "Accept": "application/json" #Return the response in a JSON format
    }

    try:
        response = requests.get(url, headers=headers) #sends a GET request
        return response.json(), None #return as JSON
    except Exception as e:
        return None, f"Twitch channels request error: {e}"

def query_anilist_by_name(name, access_token):
    query_obj = { #sets what data to retrieve based on anilist api reference
        "query": """
            query ($search: String) {
                Page(perPage: 1) {
                    media(search: $search, type: ANIME){
                    id
                    title {romaji english native}
                    description
                    coverImage {large}
                    genres
                    popularity
                    siteUrl
                }
            }
        }
    """,
    'variables': {"search": name},
    }

    headers = {  #sets the standard header for json
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    if access_token: #if the user has an access token, attach the bearer token
        headers["Authorization"] = f"Bearer {access_token}"

    try:
        res = requests.post("https://graphql.anilist.co", json=query_obj, headers = headers) #send a POST request with the payload to anilist
        return res.json(), None #return JSON results, return none if there was an error
    except Exception as e:
        return None, f"AniList request error: {e}" #return error message if failed

@app.route("/", methods=["GET"])
def home():
  return render_template("index.html") #returns the html format for home page


@app.route("/authorize", methods=["GET"])
def authorize():
  auth_params = {  #sets up the parameters for anilist OAuth
      "response_type": "code", #return an authorization code
      "client_id": anilist_clientid,
      "redirect_uri": anilist_redirect_url,
  }

  req = requests.Request("GET", "https://anilist.co/api/v2/oauth/authorize", params=auth_params) #creates a http request
  prepared = req.prepare() #formats the url
  return redirect(prepared.url) #redirect user to anilist login page


@app.route("/callback", methods=["GET"])
def callback():
  global ani_access_token, ani_refresh_token, ani_token_expires_at #makes any changes to these variables in this def global
  code = request.args.get("code") #get a authorization code

  if not code:
    return "Missing authorization code", 400 #400 error if bad request

  payload = { #data for access token for anilist
      "grant_type": "authorization_code",
      "code": code,
      "client_id": anilist_clientid,
      "client_secret": anilist_client_secret,
      "redirect_uri": anilist_redirect_url,
  }
  
  headers = { #header for token exchange request
      "Content-Type": "application/x-www-form-urlencoded",
      "Accept": "application/json",
  }

  try:
    token_res = requests.post("https://anilist.co/api/v2/oauth/token", data=payload, headers=headers) #gets a token from anilist
    json_data = token_res.json() #parse token as JSON

    ani_access_token = json_data.get("access_token") #extract and stores token
    ani_refresh_token = json_data.get("refresh_token") #extract and stores token
    ani_token_expires_at = int(time.time()) + json_data.get("expires_in", 0) #sets expiration

    #return a success message if authorized
    return f"""
            <h2>AniList Authorized</h2>
            <p>Token received. You can now return to the home page and run searches.</p>
            <pre>{json_data.get("expires_in")}</pre>
            <p><a href="/">Back</a></p>
        """
  except Exception as e:
    return f"Failed to process Anilist token response: {e}", 500 #return 500 error if failed


@app.route("/search", methods=["POST"]) 
def search():
  anime_name = request.form.get("anime", "").strip() #grab anime search
  if not anime_name:
    return "Missing anime name", 400 #return 400 error if title is empty

  ani_json, err = query_anilist_by_name(anime_name, ani_access_token) #query Anilist for matching anime data from user input
  if err:
    return f"<h2>Error</h2><pre>{err}</pre>", 500 #return 500 error if failed

  data_field = ani_json.get("data") if isinstance(ani_json, dict) else None #extract data
  page_field = data_field.get("Page") if isinstance(data_field, dict) else None #extract page
  media_list = page_field.get("media") if isinstance(page_field, dict) else None #extract media

  media = None #variable to hold dictionary
  if isinstance(media_list, list) and len(media_list) > 0:
    media = media_list[0] #selects the first result that pops up

  if not media: #in case anilist return no results, return no results
    return (
        f'<h2>No results from AniList for "{anime_name}"</h2><p><a'
        ' href="/">Back</a></p>'
    )

  titles = media.get("title") if isinstance(media, dict) else {} #extract title dictionary
  if not titles:
    titles = {}

  chosen_title = ( #selects title format based on priority
      titles.get("english")
      or titles.get("romaji")
      or titles.get("native")
      or anime_name
  )

  twitch_token, err2 = get_twitch_token() #gets a twitch API token
  if err2:
    return f"<h2>Twitch token error</h2><pre>{err2}</pre>", 500 #return a 500 error if failed

  twitch_json, err3 = query_twitch_channels(chosen_title, twitch_token) #search twitch channel for matching name
  if err3:
    return f"<h2>Twitch query error</h2><pre>{err3}</pre>", 500 #return an error if twitch search failed

  cover_image = media.get("coverImage") if isinstance(media, dict) else {} #extract cover image
  cover = cover_image.get("large", "") if isinstance(cover_image, dict) else ""#extract large cover image

  genres_list = media.get("genres") if isinstance(media, dict) else [] #extract the list of anime genres
  genres = ", ".join(genres_list) if isinstance(genres_list, list) else "" #join the genres into a single string

  popularity = str(media.get("popularity", "N/A") if isinstance(media, dict) else "N/A") #extract the anime popularity as a string
  description = strip_htmltags(media.get("description", "") if isinstance(media, dict) else "" ) #cleans the html for anime's description
  site_url = media.get("siteUrl", "#") if isinstance(media, dict) else "#" #extracts anilist webpage for anime

  raw_twitch_list = [] #empty list for raw twitch channels
  if isinstance(twitch_json, dict): #checks if twitch response is a valid dictionary
    raw_twitch_list = twitch_json.get("data", []) 

  formatted_twitch_list = [] #list for formated twitch channels
  for ch in raw_twitch_list:  #goes through each twitch channel found
    if isinstance(ch, dict): #Makes sure its a valid dictionary
      formatted_twitch_list.append({ #inserts all the anime and twitch data
          "name": ch.get("display_name")
          or ch.get("broadcaster_login")
          or "unknown",
          "login": ch.get("broadcaster_login", ""),
          "title": ch.get("title", ""),
          "is_live": ch.get("is_live", False),
          "viewers": str(ch.get("viewer_count"))
          if ch.get("viewer_count")
          else "",
          "thumbnail_url": ch.get("thumbnail_url", ""),
      })

  return render_template( #returns the data in the html format in results.html
      "results.html",
      anime_title=chosen_title,
      cover=cover,
      genres=genres,
      popularity=popularity,
      description=description,
      site_url=site_url,
      chosen_title=chosen_title,
      twitch_list=formatted_twitch_list,
  )


if __name__ == "__main__":
  app.run(port=port, debug=True) #Start local server on port 8000 with debug mode on