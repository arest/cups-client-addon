import os
import json
import cups
import tempfile
import requests
from aiohttp import web, ClientSession
from slugify import slugify
import logging
import yaml

_LOGGER = logging.getLogger(__name__)

class CupsClientService:
    def __init__(self):
        # Load config
        with open('/data/options.json') as config_file:
            self.config = json.load(config_file)

        # Initialize CUPS connection
        self.cups_conn = cups.Connection(
            host=self.config['cups_server'],
            port=self.config['cups_port']
        )

        # Home Assistant API token from the environment
        self.supervisor_token = os.environ.get('SUPERVISOR_TOKEN')
        if not self.supervisor_token:
            _LOGGER.warning("No supervisor token found - Home Assistant API calls will not work")

        # Get header names from config
        self.headers = self.config.get('header_names', {
            'printer_name': 'X-Printer-Name',
            'printer_ip': 'X-Printer-IP',
            'printer_port': 'X-Printer-Port',
            'job_id': 'X-Print-Job-ID',
            'job_type': 'X-Printer-Job-Type',
            'paper_size': 'X-Paper-Size',
            'page_range': 'X-Page-Range'
        })

        # Added default printer settings
        self.default_printer = self.config.get('default_printer', '')
        self.default_printer_ip = self.config.get('default_printer_ip', '')

    async def register_service(self):
        """Register this addon as a Home Assistant service."""
        if not self.supervisor_token:
            _LOGGER.error("No supervisor token available - cannot register Home Assistant service")
            return

        async with ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {self.supervisor_token}",
                "Content-Type": "application/json",
            }

            # Load service definition from YAML file
            try:
                # Look for services.yaml in the root directory
                with open('/data/services.yaml', 'r') as f:
                    service_def = yaml.safe_load(f)
            except FileNotFoundError:
                # Fallback to the services.yaml in the current directory
                try:
                    with open('services.yaml', 'r') as f:
                        service_def = yaml.safe_load(f)
                except FileNotFoundError:
                    _LOGGER.error("services.yaml not found in /data/ or current directory")
                    return
                except yaml.YAMLError as e:
                    _LOGGER.error("Error parsing services.yaml: %s", str(e))
                    return
            except yaml.YAMLError as e:
                _LOGGER.error("Error parsing services.yaml: %s", str(e))
                return

            if not service_def or 'cups_client' not in service_def:
                _LOGGER.error("Invalid service definition in services.yaml")
                return

            # Register the service
            try:
                service_data = {
                    "domain": "cups_client",
                    "service": "print_pdf",
                    "target": {
                        "entity_id": [],  # Empty list means service is available globally
                        "device_id": [],
                        "area_id": []
                    },
                    "service_data": service_def["cups_client"]["print_pdf"]
                }

                async with session.post(
                    "http://supervisor/core/api/services/register",
                    headers=headers,
                    json=service_data
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Successfully registered Home Assistant service cups_client.print_pdf")
                    elif response.status == 409:
                        # Service already exists, this is fine
                        _LOGGER.info("Service cups_client.print_pdf already registered")
                    else:
                        response_text = await response.text()
                        _LOGGER.error("Failed to register Home Assistant service: %s - %s",
                                    response.status, response_text)
                        # Try to continue even if service registration fails
            except Exception as e:
                _LOGGER.error("Error registering service: %s", str(e))
                # Continue even if service registration fails

    async def handle_print_request(self, request):
        try:
            # Get request data
            data = await request.json()
            endpoint = data.get('endpoint', self.config['default_endpoint'])
            api_key = data.get('api_key', self.config.get('default_api_key', ''))

            # Prepare headers for the PDF request
            headers = {}
            if api_key:
                headers['X-API-KEY'] = api_key

            # Fetch PDF from endpoint
            response = requests.get(endpoint, headers=headers, stream=True)
            response.raise_for_status()

            if response.headers.get('content-type') != 'application/pdf':
                raise ValueError("Response is not a PDF file")

            # Extract printer information and print settings from headers using customizable header names
            printer_name = (
                response.headers.get(self.headers['printer_name']) or  # First try from headers
                data.get('printer_name') or  # Then from service call
                self.default_printer  # Finally from default config
            )
            printer_ip = (
                response.headers.get(self.headers['printer_ip']) or  # First try from headers
                data.get('printer_ip') or  # Then from service call
                self.default_printer_ip  # Finally from default config
            )
            printer_port = response.headers.get(self.headers['printer_port'], "631")
            job_id = response.headers.get(self.headers['job_id'])
            job_type = response.headers.get(self.headers['job_type'], "raw")

            # Get paper size and page range from headers, fallback to request data or defaults
            paper_size = (
                response.headers.get(self.headers['paper_size']) or
                data.get('paper_size') or
                self.config.get('default_paper_size', 'A4')
            )
            page_range = (
                response.headers.get(self.headers['page_range']) or
                data.get('page_range') or
                ''
            )

            # Create temporary file for PDF
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp_file:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        tmp_file.write(chunk)
                tmp_file_path = tmp_file.name

            try:
                # Prepare print options
                print_options = {
                    'job-type': job_type,
                    'media': paper_size
                }

                # Add page ranges if specified
                if page_range:
                    print_options['page-ranges'] = page_range

                # Send to printer
                print_job_id = self.cups_conn.printFile(
                    printer_name,
                    tmp_file_path,
                    f"Job_{slugify(job_id if job_id else 'print')}",
                    print_options
                )

                # Notify Home Assistant about successful print job
                if self.supervisor_token:
                    await self.notify_ha(
                        f"Print job {print_job_id} sent to {printer_name}\n"
                        f"Paper size: {paper_size}\n"
                        f"Pages: {page_range if page_range else 'all'}"
                    )

                return web.json_response({
                    "success": True,
                    "message": "Print job submitted successfully",
                    "job_id": print_job_id,
                    "printer": {
                        "name": printer_name,
                        "ip": printer_ip,
                        "port": printer_port
                    },
                    "print_options": {
                        "paper_size": paper_size,
                        "page_range": page_range if page_range else "all"
                    }
                })

            finally:
                # Clean up temporary file
                os.unlink(tmp_file_path)

        except json.JSONDecodeError:
            return web.json_response({
                "success": False,
                "error": "Invalid JSON in request"
            }, status=400)

        except requests.RequestException as e:
            return web.json_response({
                "success": False,
                "error": f"Failed to fetch PDF: {str(e)}"
            }, status=500)

        except Exception as e:
            return web.json_response({
                "success": False,
                "error": str(e)
            }, status=500)

    async def notify_ha(self, message):
        """Send a notification to Home Assistant."""
        if not self.supervisor_token:
            return

        async with ClientSession() as session:
            headers = {
                "Authorization": f"Bearer {self.supervisor_token}",
                "Content-Type": "application/json",
            }

            notification = {
                "message": message,
                "title": "CUPS Print Service"
            }

            try:
                async with session.post(
                    "http://supervisor/core/api/services/persistent_notification/create",
                    headers=headers,
                    json=notification
                ) as response:
                    if response.status != 200:
                        _LOGGER.error("Failed to send notification: %s", await response.text())
            except Exception as e:
                _LOGGER.error("Error sending notification: %s", str(e))

async def main():
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    _LOGGER.info("Starting CUPS Client Service")

    # Initialize service
    service = CupsClientService()

    # Create web application
    app = web.Application()
    app.router.add_post('/api/print', service.handle_print_request)

    # Register Home Assistant service
    _LOGGER.info("Registering Home Assistant service...")
    await service.register_service()
    
    _LOGGER.info("CUPS Client Service started successfully")

    return app

if __name__ == '__main__':
    web.run_app(main(), port=8099, host='0.0.0.0')