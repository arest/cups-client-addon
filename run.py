import os
import json
import cups
import tempfile
import requests
import traceback
from aiohttp import web, ClientSession
from slugify import slugify
import logging
import yaml
import uuid
import time

# Set more verbose logging
_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

# Define Home Assistant share directory path
HA_SHARE_DIR = "/share"
HA_TEMP_DIR = os.path.join(HA_SHARE_DIR, "cups_temp")

class CupsClientService:
    def __init__(self):
        # Load config
        with open('/data/options.json') as config_file:
            self.config = json.load(config_file)
            _LOGGER.debug("Loaded configuration: %s", self.config)

        # Ensure the temporary directory in the share folder exists
        self._ensure_temp_dir()

        # Initialize CUPS connection
        try:
            cups_host = self.config['cups_server']
            cups_port = self.config['cups_port']

            _LOGGER.debug("Attempting to connect to CUPS server at %s:%s", cups_host, cups_port)

            # Try to establish the connection
            self.cups_conn = cups.Connection(
                host=cups_host,
                port=cups_port
            )

            # Verify connection by getting server info
            server_info = self.cups_conn.getPrinters()
            _LOGGER.debug("CUPS connection established to %s:%s - Server info: %s",
                         cups_host, cups_port, server_info)

            # Test if we can get printer list
            printers = self.cups_conn.getPrinters()
            _LOGGER.debug("Found %d printers on CUPS server: %s",
                          len(printers), list(printers.keys()))

        except cups.IPPError as e:
            _LOGGER.error("CUPS IPP Error when connecting: %s", str(e))
            _LOGGER.debug(traceback.format_exc())
            raise
        except Exception as e:
            _LOGGER.error("Failed to connect to CUPS server: %s", str(e))
            _LOGGER.debug(traceback.format_exc())
            raise

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
        _LOGGER.debug("Default printer configured: %s, IP: %s",
                     self.default_printer, self.default_printer_ip)

        # Get debug file retention setting
        self.keep_temp_files = self.config.get('keep_temp_files', False)
        _LOGGER.debug("Keep temporary files for debugging: %s", self.keep_temp_files)

    def _ensure_temp_dir(self):
        """Ensure the temporary directory in the share folder exists."""
        try:
            if not os.path.exists(HA_TEMP_DIR):
                os.makedirs(HA_TEMP_DIR)
                _LOGGER.info("Created temporary directory in share folder: %s", HA_TEMP_DIR)

            # Test write permissions
            test_file = os.path.join(HA_TEMP_DIR, "write_test.tmp")
            with open(test_file, 'w') as f:
                f.write("test")
            os.unlink(test_file)
            _LOGGER.debug("Temporary directory %s is writable", HA_TEMP_DIR)
        except Exception as e:
            _LOGGER.error("Failed to create or access temporary directory: %s", str(e))
            _LOGGER.debug(traceback.format_exc())

    async def handle_print_request(self, request):
        tmp_file_path = None

        try:
            _LOGGER.debug("Received print request from %s", request.remote)

            # Get request data
            data = await request.json()
            _LOGGER.debug("Request data: %s", data)

            endpoint = data.get('endpoint', self.config['default_endpoint'])
            api_key = data.get('api_key', self.config.get('default_api_key', ''))
            _LOGGER.debug("Using endpoint: %s", endpoint)

            # Get print parameters from request data first, then fall back to other sources
            printer_name = data.get('printer_name', None)
            page_range = data.get('page_range', None)
            paper_size = data.get('paper_size', None)

            _LOGGER.debug("Print parameters from request data - Printer: %s, Page Range: %s, Paper Size: %s",
                         printer_name or 'Not specified',
                         page_range or 'Not specified',
                         paper_size or 'Not specified')

            # Prepare headers for the PDF request
            headers = {}
            if api_key:
                headers['X-API-KEY'] = api_key
                _LOGGER.debug("Added API key to request headers")

            # Fetch PDF from endpoint
            _LOGGER.debug("Fetching PDF from endpoint: %s", endpoint)
            try:
                response = requests.get(endpoint, headers=headers, stream=True)
                response.raise_for_status()
                _LOGGER.debug("PDF fetch response status: %d", response.status_code)
                _LOGGER.debug("Response headers: %s", response.headers)

                # Handle 204 No Content response gracefully
                if response.status_code == 204:
                    _LOGGER.info("Received HTTP 204 No Content response - no print job available")

                    # Notify Home Assistant about no available print jobs
                    if self.supervisor_token:
                        await self.notify_ha("No print jobs available at this time")

                    return web.json_response({
                        "success": True,
                        "message": "No print jobs available",
                        "status_code": 204
                    })

            except requests.RequestException as e:
                _LOGGER.error("Failed to fetch PDF: %s", str(e))
                _LOGGER.debug(traceback.format_exc())
                return web.json_response({
                    "success": False,
                    "error": f"Failed to fetch PDF: {str(e)}"
                }, status=500)

            # Only check content type if we didn't get a 204 response
            if response.headers.get('content-type') != 'application/pdf':
                error_msg = f"Response is not a PDF file. Content-Type: {response.headers.get('content-type')}"
                _LOGGER.error(error_msg)
                return web.json_response({
                    "success": False,
                    "error": error_msg
                }, status=500)

            # Extract printer information and print settings from headers using customizable header names
            # Only use header values if not already set from request data
            if not printer_name and 'printer_name' in self.headers:
                printer_name = response.headers.get(self.headers['printer_name'])
            if not printer_name:
                printer_name = self.default_printer

            printer_ip = None
            if 'printer_ip' in self.headers:
                printer_ip = response.headers.get(self.headers['printer_ip'])
            if not printer_ip:
                printer_ip = data.get('printer_ip') or self.default_printer_ip

            printer_port = "631"  # Default CUPS port
            if 'printer_port' in self.headers:
                port_from_header = response.headers.get(self.headers['printer_port'])
                if port_from_header:
                    printer_port = port_from_header

            job_id = None
            if 'job_id' in self.headers:
                job_id = response.headers.get(self.headers['job_id'])

            job_type = "raw"  # Default job type
            if 'job_type' in self.headers:
                type_from_header = response.headers.get(self.headers['job_type'])
                if type_from_header:
                    job_type = type_from_header

            # Only use header values for paper size and page range if not already set from request data
            if not paper_size and 'paper_size' in self.headers:
                paper_size = response.headers.get(self.headers['paper_size'])
            if not paper_size:
                paper_size = self.config.get('default_paper_size', 'A4')

            if not page_range and 'page_range' in self.headers:
                page_range = response.headers.get(self.headers['page_range'])
            # No default for page_range as empty means print all pages

            _LOGGER.debug("Final print parameters - Printer: %s, IP: %s, Port: %s, Job ID: %s, Paper: %s, Range: %s",
                         printer_name, printer_ip, printer_port, job_id, paper_size, page_range or "all")

            if not printer_name:
                error_msg = "No printer name provided and no default printer configured"
                _LOGGER.error(error_msg)
                return web.json_response({
                    "success": False,
                    "error": error_msg
                }, status=400)

            # Create temporary file for PDF
            try:
                # Use Home Assistant share directory for temp files instead of system temp
                pdf_filename = f"print_{uuid.uuid4().hex}.pdf"
                tmp_file_path = os.path.join(HA_TEMP_DIR, pdf_filename)

                with open(tmp_file_path, 'wb') as tmp_file:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            tmp_file.write(chunk)

                _LOGGER.debug("PDF saved to share folder temporary file: %s", tmp_file_path)

                # Validate the temporary file
                if not self._validate_temp_file(tmp_file_path):
                    return web.json_response({
                        "success": False,
                        "error": "Failed to validate temporary PDF file"
                    }, status=500)

            except Exception as e:
                _LOGGER.error("Failed to save PDF to temp file: %s", str(e))
                _LOGGER.debug(traceback.format_exc())
                return web.json_response({
                    "success": False,
                    "error": f"Failed to save PDF: {str(e)}"
                }, status=500)

            try:
                # Prepare print options
                print_options = {
                    'job-type': job_type,
                    'media': paper_size
                }

                # Add page ranges if specified
                if page_range:
                    print_options['page-ranges'] = page_range

                _LOGGER.debug("Print options: %s", print_options)

                # Check if printer exists before printing
                try:
                    printers = self.cups_conn.getPrinters()
                    _LOGGER.debug("Available printers: %s", list(printers.keys()))

                    if printer_name not in printers:
                        _LOGGER.error("Printer '%s' not found in CUPS server. Available printers: %s",
                                     printer_name, list(printers.keys()))
                        return web.json_response({
                            "success": False,
                            "error": f"Printer '{printer_name}' not found in CUPS server",
                            "available_printers": list(printers.keys())
                        }, status=404)
                except Exception as e:
                    _LOGGER.error("Failed to get printer list: %s", str(e))
                    _LOGGER.debug(traceback.format_exc())

                # Send to printer
                try:
                    _LOGGER.debug("Sending print job to printer '%s'", printer_name)
                    _LOGGER.debug("Temp file path: %s (exists: %s, size: %s bytes)",
                                 tmp_file_path,
                                 os.path.exists(tmp_file_path),
                                 os.path.getsize(tmp_file_path) if os.path.exists(tmp_file_path) else 'N/A')

                    print_job_id = self.cups_conn.printFile(
                        printer_name,
                        tmp_file_path,
                        f"Job_{slugify(job_id if job_id else 'print')}",
                        print_options
                    )
                    _LOGGER.info("Print job %s sent successfully to printer %s", print_job_id, printer_name)
                except cups.IPPError as e:
                    error_msg = f"CUPS IPP Error: {str(e)}"
                    _LOGGER.error(error_msg)
                    _LOGGER.debug(traceback.format_exc())

                    # Check for the specific "No such file or directory" error
                    if e.args and len(e.args) >= 2 and "No such file or directory" in str(e.args[1]):
                        # This could be due to:
                        # 1. The printer doesn't exist
                        # 2. The file path is not accessible to CUPS
                        # 3. Permissions issues

                        cups_accessible = os.access(HA_TEMP_DIR, os.R_OK | os.W_OK)

                        _LOGGER.error("CUPS 'No such file' error details:")
                        _LOGGER.error("- Printer name: %s", printer_name)
                        _LOGGER.error("- File path: %s", tmp_file_path)
                        _LOGGER.error("- File exists: %s", os.path.exists(tmp_file_path))
                        _LOGGER.error("- File readable: %s", os.access(tmp_file_path, os.R_OK) if os.path.exists(tmp_file_path) else "N/A")
                        _LOGGER.error("- CUPS can access temp dir: %s", cups_accessible)
                        _LOGGER.error("- Temp directory permissions: %s", oct(os.stat(HA_TEMP_DIR).st_mode & 0o777))
                        _LOGGER.error("- File permissions: %s", oct(os.stat(tmp_file_path).st_mode & 0o777) if os.path.exists(tmp_file_path) else "N/A")

                        # Get CUPS server status
                        try:
                            server_status = self.cups_conn.adminGetServerSettings()
                            _LOGGER.debug("CUPS server settings: %s", server_status)
                        except Exception as server_e:
                            _LOGGER.error("Failed to get CUPS server settings: %s", str(server_e))

                        return web.json_response({
                            "success": False,
                            "error": "CUPS print error: File or printer not found",
                            "details": {
                                "original_error": str(e),
                                "printer": printer_name,
                                "file_path": tmp_file_path,
                                "file_exists": os.path.exists(tmp_file_path),
                                "file_readable": os.access(tmp_file_path, os.R_OK) if os.path.exists(tmp_file_path) else False,
                                "temp_dir": HA_TEMP_DIR,
                                "temp_dir_accessible": cups_accessible,
                                "diagnostics": "Check if the printer exists and if CUPS has access to the share folder"
                            }
                        }, status=500)

                    return web.json_response({
                        "success": False,
                        "error": error_msg
                    }, status=500)
                except Exception as e:
                    error_msg = f"CUPS printing error: {str(e)}"
                    _LOGGER.error(error_msg)
                    _LOGGER.debug(traceback.format_exc())
                    return web.json_response({
                        "success": False,
                        "error": error_msg
                    }, status=500)

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
                # Clean up temporary file if not in debug mode
                if tmp_file_path and os.path.exists(tmp_file_path) and not self.keep_temp_files:
                    try:
                        os.unlink(tmp_file_path)
                        _LOGGER.debug("Temporary file %s deleted", tmp_file_path)
                    except Exception as e:
                        _LOGGER.warning("Failed to delete temporary file %s: %s", tmp_file_path, str(e))
                        _LOGGER.debug(traceback.format_exc())
                elif tmp_file_path and os.path.exists(tmp_file_path) and self.keep_temp_files:
                    _LOGGER.debug("Keeping temporary file %s for debugging (keep_temp_files=True)", tmp_file_path)

                # Periodically clean up old temporary files (if not keeping files for debugging)
                if not self.keep_temp_files:
                    self._cleanup_old_temp_files()

        except json.JSONDecodeError as e:
            _LOGGER.error("Invalid JSON in request: %s", str(e))
            _LOGGER.debug(traceback.format_exc())
            return web.json_response({
                "success": False,
                "error": f"Invalid JSON in request: {str(e)}"
            }, status=400)

        except Exception as e:
            _LOGGER.error("Unhandled exception in print request handler: %s", str(e))
            _LOGGER.error(traceback.format_exc())
            return web.json_response({
                "success": False,
                "error": f"Server error: {str(e)}"
            }, status=500)

    def _validate_temp_file(self, file_path):
        """Validate that the temporary file exists and is readable."""
        try:
            if not os.path.exists(file_path):
                _LOGGER.error("Temporary file does not exist: %s", file_path)
                return False

            if not os.access(file_path, os.R_OK):
                _LOGGER.error("Temporary file is not readable: %s", file_path)
                return False

            file_size = os.path.getsize(file_path)
            if file_size == 0:
                _LOGGER.error("Temporary file is empty: %s", file_path)
                return False

            _LOGGER.debug("Temporary file validated: %s (size: %d bytes)", file_path, file_size)
            return True
        except Exception as e:
            _LOGGER.error("Error validating temporary file: %s - %s", file_path, str(e))
            _LOGGER.debug(traceback.format_exc())
            return False

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
                _LOGGER.debug("Sending notification to Home Assistant")
                async with session.post(
                    "http://supervisor/core/api/services/persistent_notification/create",
                    headers=headers,
                    json=notification
                ) as response:
                    if response.status != 200:
                        _LOGGER.error("Failed to send notification: %s", await response.text())
                    else:
                        _LOGGER.debug("Notification sent successfully")
            except Exception as e:
                _LOGGER.error("Error sending notification: %s", str(e))
                _LOGGER.debug(traceback.format_exc())

    def _cleanup_old_temp_files(self):
        """Clean up old temporary files from the share folder."""
        try:
            # Don't clean up if we're keeping temp files for debugging
            if self.keep_temp_files:
                _LOGGER.debug("Skipping temp file cleanup - keep_temp_files is enabled")
                return

            # Clean up files older than 1 hour
            current_time = time.time()
            one_hour_ago = current_time - 3600

            count = 0
            if os.path.exists(HA_TEMP_DIR):
                for filename in os.listdir(HA_TEMP_DIR):
                    file_path = os.path.join(HA_TEMP_DIR, filename)
                    if os.path.isfile(file_path) and filename.startswith("print_") and filename.endswith(".pdf"):
                        # Check file age
                        file_mod_time = os.path.getmtime(file_path)
                        if file_mod_time < one_hour_ago:
                            try:
                                os.unlink(file_path)
                                count += 1
                            except Exception as e:
                                _LOGGER.warning("Failed to delete old temp file %s: %s", file_path, str(e))

            if count > 0:
                _LOGGER.debug("Cleaned up %d old temporary files", count)
        except Exception as e:
            _LOGGER.warning("Error during temp file cleanup: %s", str(e))

async def main():
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    _LOGGER.info("Starting CUPS Client Service")

    # Initialize service
    try:
        service = CupsClientService()
    except Exception as e:
        _LOGGER.error("Failed to initialize CUPS Client Service: %s", str(e))
        _LOGGER.error(traceback.format_exc())
        raise

    # Create web application
    app = web.Application()
    app.router.add_post('/api/print', service.handle_print_request)

    # Register Home Assistant service

    _LOGGER.info("CUPS Client Service started successfully")

    return app

if __name__ == '__main__':
    web.run_app(main(), port=8099, host='0.0.0.0', print=False)