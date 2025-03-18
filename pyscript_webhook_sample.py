import aiohttp

async def print_pdf(pdf_url):
    cups_addon_url = "http://localhost:8099/api/print"
    # Prepare the request data with the PDF URL as the endpoint
    request_data = {
        "endpoint": pdf_url
    }

    # Send the request to the CUPS client addon using aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(cups_addon_url, json=request_data) as resp:
            status = resp.status
            log.info(f"Cups Client response status {status}")

@webhook_trigger("print_webhook")
def print_webhook(payload):
    log.info(f"It ran! {payload}")

    # Verify that payload contains pdf_url
    if not payload or 'pdf_url' not in payload:
        log.error("Missing required 'pdf_url' in payload")
        return {"success": False, "error": "Missing required 'pdf_url' in payload"}

    pdf_url = payload['pdf_url']

    print_pdf(pdf_url)