import os
import time
import datetime
import ssl
import smtplib
import schedule
from supabase import create_client
from dotenv import load_dotenv
import requests
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

load_dotenv()
SUPABASE_URL      = os.getenv("SUPABASE_URL") #base url for the STAGGING ENVIRONMENT currently, must upadte this to the production environment
SUPABASE_KEY      = os.getenv("SUPABASE_KEY") 
API_PRIVATE_TOKEN = os.getenv("API_PRIVATE_TOKEN") #token for the stagging enviornment 
BASE_URL          = os.getenv("BASE_URL")
EMAIL_SENDER      = os.getenv("EMAIL_SENDER")
EMAIL_RECEIVERS   = os.getenv("EMAIL_RECEIVERS").split(",")
EMAIL_PASSWORD    = os.getenv("EMAIL_PASSWORD")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_previously_saved_data(): #access the data from the prior function call 
    """
    Fetches all rows from the 'PriorReadings' table, ordered by id ascending.
    Returns a list of dicts, or None if the table is empty.
    """
    data = (
        supabase
        .table("PriorReadings")
        .select("*")
        .order("id", desc=False)
        .execute()
        .data
    )
    return data if data else None


def read_in_mapping():
    """
    Reads 'mapping.json' (which should be a list of product dicts with keys
    'productName', 'apiCode', 'matchScore', etc.) and returns the list.
    """
    with open("mapping.json", "r") as f: #reads in the mapping file which is the products to their corressponding api codes
        return json.load(f)


def get_spreads(api_codes):
    """
    Calls FizTrade's GetPricesForProducts endpoint once with a list of api_codes.
    Returns a dict mapping each api_code to a tuple (spread, askPercise, bidPercise).
    The response list is assumed to be in the same order as api_codes, so we
    index-match instead of relying on a key in the response objects.
    If an entry is missing or malformed, that code maps to None.
    """


    endpoint = f"{BASE_URL}/FizServices/GetPricesForProducts/{API_PRIVATE_TOKEN}" #access the endpoint with an array of all of the products int ehAPI
    headers = {"Content-Type": "application/json"}
    response = requests.post(endpoint, json=api_codes, headers=headers)
    print(f"Response status code: {response.status_code}")  # Debugging output
    spreads_map = {code: None for code in api_codes} #maps all the spreads and codes together 

    try:
        data_list = response.json() #parse the response into json
    except ValueError:
        return spreads_map

    
    if response.status_code == 200 and isinstance(data_list, list):
        for idx, code in enumerate(api_codes):
            if idx < len(data_list):
                entry = data_list[idx] or {}
                tiers = entry.get("tiers", {})
                tier1 = tiers.get("1", {})
                spread = tier1.get("spread")
                ask    = tier1.get("askPercise")
                bid    = tier1.get("bidPercise")
                spreads_map[code] = (spread, ask, bid)
            else:
                spreads_map[code] = None
    return spreads_map 


def get_other_remaining_data(api_codes):
    """
    Calls FizTrade's GetPremiums endpoint for a list of api_codes.
    Returns the JSON response as a dict (or list of dicts). Raises on HTTP error.
    """
    endpoint = f"{BASE_URL}/FizServices/GetPremiums/{API_PRIVATE_TOKEN}"
    headers  = {"Content-Type": "application/json"}
    body     = api_codes

    # DEBUG: print what we’re sending
    print(f"[get_other_remaining_data] POST {endpoint}  payload={body!r}")

    response = requests.post(endpoint, json=body, headers=headers)

    # DEBUG: print status and raw text
    print(f"[get_other_remaining_data] status={response.status_code}")
    print(f"[get_other_remaining_data] response.text={response.text}")

    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        print("[get_other_remaining_data] invalid JSON")
        return None


def update_table_todays():
    """
    For every item in mapping.json:
      1) Batch‐fetch all spreads via get_spreads().
      2) Individually fetch premium/formula via get_other_remaining_data().
      3) Upsert into 'PriorReadings' (only the columns that exist in the schema).
    """
    mapping = read_in_mapping() #gets the mapping of products to their api codes 
    api_codes = [item.get("apiCode") for item in mapping] #gets the codes and maps them to an array, (uses the get function to avoid KeyError)
    spreads_map = get_spreads(api_codes) #gets the spreads for all of the products in the mapping file
    other_info_raw = get_other_remaining_data(api_codes)

    # build a flat map apiCode → info_list, handling both Dict and List[Dict]
    if isinstance(other_info_raw, dict):
        other_info_map = other_info_raw
    elif isinstance(other_info_raw, list):
        other_info_map = {
            code: info_list
            for entry in other_info_raw
            if isinstance(entry, dict)
            for code, info_list in entry.items()
        }
    else:
        other_info_map = {}

    for item in mapping: #for each item in the mapping file, we will 
        troubleSome = False #intially the trouble some variable is set to false, but this is flagged if an error occurs in the process or we are unsure on an item as we go
        product = item.get("productName") #get the product name 
        apiCode = item.get("apiCode") #get the api code for the product 
        if item.get("matchScore", 0) != 1: #if we are uncofident in the association we need to make note of that and this will appear in the database 
            troubleSome = True

        # 1) GET SPREAD from spreads_map
        spread_tuple = spreads_map.get(apiCode) #get the spread from the spreads map using the api code
        if spread_tuple:
            spread = spread_tuple[0]
        else:
            spread = None
            troubleSome = True

        # 2) GET OTHER DATA (percentBidDelta, fixedAskDelta, …)
        percentBid = fixedAsk = percentAsk = fixedBid = None
        # result_list is a List[dict], one per tier—grab the first tier
        result_list = other_info_map.get(apiCode, [])
        if not result_list:
            troubleSome = True
        else:
            info = result_list[0]
            percentBid = info.get("percentBidDelta")
            fixedAsk   = info.get("fixedAskDelta")
            percentAsk = info.get("percentAskDelta")
            fixedBid   = info.get("fixedBidDelta")


        # 3) UPSERT into 'PriorReadings' (only existing columns)
        # classify percent/fixed mix for every possible on/off combo
        # “on” means non-None and != 0
        pb_on = percentBid   is not None and percentBid   != 0
        pa_on = percentAsk   is not None and percentAsk   != 0
        fa_on = fixedAsk     is not None and fixedAsk     != 0
        fb_on = fixedBid     is not None and fixedBid     != 0

        if not (pb_on or pa_on or fa_on or fb_on):
            percentFixedMixed = None
        # all four non-zero
        elif pb_on and pa_on and fa_on and fb_on:
            percentFixedMixed = "MIX"
        # PURE PERCENT (any combination of pb/pa on, both fixed off)
        elif (pb_on or pa_on) and not (fa_on or fb_on):
            percentFixedMixed = "PERCENT"
        # PURE FIXED   (any combination of fa/fb on, both percent off)
        elif (fa_on or fb_on) and not (pb_on or pa_on):
            percentFixedMixed = "FIXED"
        # MIX (any other case where at least one percent AND one fixed is on)
        else:
            percentFixedMixed = "MIX"

        fields = {
            "PRODUCT_NAME":       product,
            "API_ID":             apiCode,
            "SPREAD":             spread,
            "PERCENT_BID":        percentBid,
            "FIXED_ASK":          fixedAsk,
            "PERCENT_ASK":        percentAsk,
            "FIXED_BID":          fixedBid,
            "IS_TROUBLESOME":     troubleSome,
            "PERCENT_FIXED_MIX":  percentFixedMixed
        }
        existing = (
            supabase
            .table("PriorReadings")
            .select("*")
            .eq("API_ID", apiCode)
            .execute()
            .data
        )
        if existing:
            supabase.table("PriorReadings").update(fields).eq("id", existing[0]["id"]).execute()
        else:
            supabase.table("PriorReadings").insert(fields).execute()


# ─── COMPARISON AND EMAIL FORMATTING ─────────────────────────────────────────────

def make_comparison():
    """
    1) Fetch previous_data (before today's update).
    2) Run update_table_todays() to overwrite today's data.
    3) Fetch current_data (after today's update).
    4) Compare row-by-row for each field: SPREAD, MELT, PERCENT_BID, FIXED_ASK,
       PERCENT_ASK, FIXED_BID, FORMULA.
    Returns a dict with keys: "spread", "melt", "percent_bid", "fixed_ask",
    "percent_ask", "fixed_bid", "formula", each mapping to a list of changes.
    """
    previous_data = get_previously_saved_data()
    update_table_todays()
    current_data = get_previously_saved_data()


    if not previous_data or not current_data:
        return {
            "spread":      [],
            "percent_bid": [],
            "fixed_ask":   [],
            "percent_ask": [],
            "fixed_bid":   [],
            "percent_fixed_mix": []
        }

    prev_map = {row["API_ID"]: row for row in previous_data}
    curr_map = {row["API_ID"]: row for row in current_data}

    changes = {
        "spread":      [],
        "percent_bid": [],
        "fixed_ask":   [],
        "percent_ask": [],
        "fixed_bid":   [],
        "percent_fixed_mix": []
    }

    for api_code, curr_row in curr_map.items():
        prev_row = prev_map.get(api_code)
        if not prev_row:
            continue

        product_name = curr_row.get("PRODUCT_NAME", api_code)

        if curr_row.get("SPREAD") != prev_row.get("SPREAD"):
            changes["spread"].append({
                "product":         product_name,
                "previous_spread": prev_row.get("SPREAD"),
                "current_spread":  curr_row.get("SPREAD")
            })
        if curr_row.get("PERCENT_BID") != prev_row.get("PERCENT_BID"):
            changes["percent_bid"].append({
                "product":               product_name,
                "previous_percent_bid": prev_row.get("PERCENT_BID"),
                "current_percent_bid":  curr_row.get("PERCENT_BID")
            })
        if curr_row.get("FIXED_ASK") != prev_row.get("FIXED_ASK"):
            changes["fixed_ask"].append({
                "product":            product_name,
                "previous_fixed_ask": prev_row.get("FIXED_ASK"),
                "current_fixed_ask":  curr_row.get("FIXED_ASK")
            })
        if curr_row.get("PERCENT_ASK") != prev_row.get("PERCENT_ASK"):
            changes["percent_ask"].append({
                "product":              product_name,
                "previous_percent_ask": prev_row.get("PERCENT_ASK"),
                "current_percent_ask":  curr_row.get("PERCENT_ASK")
            })
        if curr_row.get("FIXED_BID") != prev_row.get("FIXED_BID"):
            changes["fixed_bid"].append({
                "product":            product_name,
                "previous_fixed_bid": prev_row.get("FIXED_BID"),
                "current_fixed_bid":  curr_row.get("FIXED_BID")
            })
        if curr_row.get("PERCENT_FIXED_MIX") != prev_row.get("PERCENT_FIXED_MIX"):
            changes["percent_fixed_mix"].append({
                "product":                 product_name,
                "previous_percent_fixed_mix": prev_row.get("PERCENT_FIXED_MIX"),
                "current_percent_fixed_mix":  curr_row.get("PERCENT_FIXED_MIX")
            })

    return changes


def format_email_html():
    """
    Builds an HTML email body with sections for:
      - Changes in Spread
      - Changes in Melt
      - Changes in Percent Bid
      - Changes in Fixed Ask
      - Changes in Percent Ask
      - Changes in Fixed Bid
      - Changes in Formula
      - Current Ask & Bid Prices (fetched in one batch)
    """
    changes = make_comparison()
    mapping = read_in_mapping()
    api_codes = [item.get("apiCode") for item in mapping]
    spreads_map = get_spreads(api_codes)

    section_order = [
        ("spread", "Changes in Spread", ["previous_spread", "current_spread"]),
        ("percent_bid", "Changes in Percent Bid", ["previous_percent_bid", "current_percent_bid"]),
        ("fixed_ask", "Changes in Fixed Ask", ["previous_fixed_ask", "current_fixed_ask"]),
        ("percent_ask", "Changes in Percent Ask", ["previous_percent_ask", "current_percent_ask"]),
        ("fixed_bid", "Changes in Fixed Bid", ["previous_fixed_bid", "current_fixed_bid"]),
        ("percent_fixed_mix", "Changes in Percent/Fixed Mix", ["previous_percent_fixed_mix", "current_percent_fixed_mix"])
    ]

    html = [
        "<html>",
        "<body>",
        f"<h2>Changes Report - {datetime.datetime.now():%Y-%m-%d %H:%M}</h2>",
        "<hr>"
    ]

    # 1) Add sections for each “change” category
    for key, title, fields in section_order:
        html.append(f"<h3>{title}</h3>")
        changes_list = changes.get(key, [])
        prev_field, curr_field = fields

        if changes_list:
            html.append("<table border='1' cellspacing='0' cellpadding='4'>")
            html.append("<tr style='background-color:#e0e0e0;'><th>Product</th><th>Previous</th><th>Current</th></tr>")
            for change in changes_list:
                prev_val = change.get(prev_field)
                curr_val = change.get(curr_field)

                def fmt(val, field_name):
                    if val is None:
                        return ""
                    # try to coerce to a float for numeric formatting
                    try:
                        num = float(val)
                    except (TypeError, ValueError):
                        # if it's not numeric, just return the raw string
                        return str(val)
                    # decide suffix based on the field name
                    if "percent" in field_name.lower():
                        return f"{num:.2f}%"
                    return f"${num:,.2f}"

                html.append(
                    "<tr>"
                    f"<td>{change['product']}</td>"
                    f"<td>{fmt(prev_val, prev_field)}</td>"
                    f"<td>{fmt(curr_val, curr_field)}</td>"
                    "</tr>"
                )
            html.append("</table>")
        else:
            html.append(f"<p>No changes in {title.lower()}.</p>")

        html.append("<br>")

    # 2) Add a final table for CURRENT ask/bid prices
    html.append("<h3>Current Ask & Bid Prices</h3>")
    html.append("<table border='1' cellspacing='0' cellpadding='4'>")
    html.append("<tr style='background-color:#e0e0e0;'><th>Product</th><th>Ask Price</th><th>Bid Price</th></tr>")

    for item in mapping:
        prod = item.get("productName", "")
        code = item.get("apiCode", "")
        spread_tuple = spreads_map.get(code) or ()
        ask_price = spread_tuple[1] if len(spread_tuple) > 1 else None
        bid_price = spread_tuple[2] if len(spread_tuple) > 2 else None

        ask_fmt = f"${ask_price:,.2f}" if ask_price is not None else ""
        bid_fmt = f"${bid_price:,.2f}" if bid_price is not None else ""
        html.append(
            "<tr>"
            f"<td>{prod}</td>"
            f"<td>{ask_fmt}</td>"
            f"<td>{bid_fmt}</td>"
            "</tr>"
        )

    html.append("</table>")
    html.append("</body>")
    html.append("</html>")
    return "\n".join(html)


def send_email_html(sender, receivers, password, html_body):
    """
    Sends an HTML email to 'receivers' from 'sender' with login 'password'.
    The email subject is "Changes as of <now>".
    """
    msg = MIMEMultipart("alternative")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    msg["Subject"] = f"Changes as of {now_str}"
    msg["From"]    = sender
    msg["To"]      = ", ".join(receivers)

    html_part = MIMEText(html_body, "html")
    msg.attach(html_part)

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receivers, msg.as_string())
        server.quit()
        print("HTML email sent successfully.")
    except smtplib.SMTPAuthenticationError:
        print("Error: Unable to login. Check your email and password.")
    except Exception as e:
        print(f"Error sending email: {e}")


def job():
    """
    1) Builds the HTML email body via format_email_html().
    2) If there are absolutely no changes in any of the seven categories, prints a
       message and does not send. Otherwise, sends the HTML email.
    """
    # decide whether to send a “no changes” notice or the full report

    html_body = format_email_html()

    send_email_html(EMAIL_SENDER, EMAIL_RECEIVERS, EMAIL_PASSWORD, html_body)
    print("Job completed successfully.")


if __name__ == "__main__":
    job()
