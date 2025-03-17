# CUPS Client Add-on for Home Assistant

This Home Assistant add-on provides a service to fetch PDFs from external endpoints and print them using CUPS (Common Unix Printing System). It acts as a bridge between your Home Assistant automations and your CUPS-enabled printers.

## Features

- 🖨️ Print PDFs directly from URLs via CUPS
- 🔄 Configurable CUPS server connection
- 🎯 Support for custom printer selection via headers
- 🔒 Secure temporary file handling
- 🔑 Optional API key authentication for external endpoints
- 📋 Detailed job status reporting
- 🔌 Native Home Assistant service integration
- 📬 Print job notifications in Home Assistant
- 📄 Customizable paper sizes and page ranges
- 🏷️ Customizable header names

## Installation

1. Add this repository to your Home Assistant add-on store:
   ```
   https://github.com/arest/cups-client-addon
   ```

2. Install the "CUPS Client Add-on" from the add-on store

3. Configure the add-on (see Configuration section)

4. Start the add-on

## Configuration

### Add-on Configuration

```yaml
cups_server: localhost
cups_port: 631
default_endpoint: "http://localhost:8000/print"
default_api_key: ""  # Optional: Default API key for the external endpoint
default_printer: ""  # Optional: Default printer name if not specified in headers or request
default_printer_ip: ""  # Optional: Default printer IP if not specified in headers or request
default_paper_size: "A4"  # Optional: Default paper size for printing
header_names:  # Optional: Customize the header names used by your PDF endpoint
  printer_name: "X-Printer-Name"
  printer_ip: "X-Printer-IP"
  printer_port: "X-Printer-Port"
  job_id: "X-Print-Job-ID"
  job_type: "X-Printer-Job-Type"
  paper_size: "X-Paper-Size"
  page_range: "X-Page-Range"
```

| Option | Description |
|--------|-------------|
| cups_server | The hostname or IP address of your CUPS server |
| cups_port | The port number of your CUPS server (usually 631) |
| default_endpoint | Default endpoint to fetch PDFs from if none specified in the request |
| default_api_key | Optional default API key to use when accessing the external endpoint |
| default_printer | Optional default printer name to use if not specified in headers or request |
| default_printer_ip | Optional default printer IP to use if not specified in headers or request |
| default_paper_size | Default paper size for printing (A4, A3, A5, Letter, Legal) |
| header_names | Customize the header names used by your PDF endpoint |

## Usage

### Home Assistant Service

Once installed, the add-on registers a service called `cups_client.print_pdf` that you can call from your automations:

```yaml
automation:
  trigger:
    platform: state
    entity_id: binary_sensor.document_ready
    to: 'on'
  action:
    service: cups_client.print_pdf
    data:
      endpoint: "http://your-server/document.pdf"
      printer_name: "Office-Printer"  # Optional: Override printer from headers/default
      printer_ip: "192.168.1.100"    # Optional: Override printer IP from headers/default
      api_key: "your-api-key"        # Optional
      paper_size: "A4"               # Optional (A4, A3, A5, Letter, Legal)
      page_range: "1-5,8,11-13"      # Optional (e.g., "1-5,8,11-13")
```

### Direct API Call

The add-on also exposes an HTTP endpoint at:
```
http://your-ha-ip:8099/api/print
```

You can call this endpoint directly:
```http
POST http://your-ha-ip:8099/api/print
Content-Type: application/json

{
  "endpoint": "http://your-server/document.pdf",
  "printer_name": "Office-Printer",  # Optional: Override printer from headers/default
  "printer_ip": "192.168.1.100",     # Optional: Override printer IP from headers/default
  "api_key": "your-api-key",         # Optional: Overrides default_api_key from config
  "paper_size": "A4",                # Optional: Overrides default_paper_size from config
  "page_range": "1-5,8,11-13"        # Optional: Specify pages to print
}
```

### Authentication

The add-on supports two ways to provide an API key for the external endpoint:

1. **Configuration Default**: Set a default API key in the add-on configuration using the `default_api_key` option.
2. **Per-Request Override**: Provide an `api_key` in the request JSON to override the default.

When an API key is provided (either way), it will be included in the request to the PDF endpoint as an `X-API-KEY` header.

### Required Headers from PDF Endpoint

The PDF endpoint can return the following headers (names are customizable in config). All headers except Content-Type are optional and will fall back to values from the request or default configuration:

| Default Header | Required | Description | Fallback Order |
|--------|----------|-------------|----------------|
| Content-Type | Yes | Must be 'application/pdf' | None |
| X-Printer-Name | No | Name of the target printer | 1. Request data<br>2. Default config |
| X-Printer-IP | No | IP address of the printer | 1. Request data<br>2. Default config |
| X-Printer-Port | No | Port of the printer | Defaults to 631 |
| X-Print-Job-ID | No | Custom job ID for tracking | None |
| X-Printer-Job-Type | No | Type of print job | Defaults to 'raw' |
| X-Paper-Size | No | Paper size for printing | 1. Request data<br>2. Default config |
| X-Page-Range | No | Pages to print | 1. Request data<br>2. Empty (print all) |

### Print Options

| Option | Description | Example |
|--------|-------------|---------|
| paper_size | Paper size for printing | "A4", "A3", "A5", "Letter", "Legal" |
| page_range | Pages to print | "1-5,8,11-13" |

### Response Format

Successful response:
```json
{
    "success": true,
    "message": "Print job submitted successfully",
    "job_id": "123",
    "printer": {
        "name": "printer_name",
        "ip": "192.168.1.100",
        "port": "631"
    },
    "print_options": {
        "paper_size": "A4",
        "page_range": "1-5,8,11-13"
    }
}
```

Error response:
```json
{
    "success": false,
    "error": "Error message description"
}
```

### Notifications

The add-on will send notifications to Home Assistant when:
- A print job is successfully submitted (including paper size and page range)
- Errors occur during printing

You can view these notifications in your Home Assistant notifications panel.

## Error Handling

The add-on handles various error scenarios:

- Invalid JSON in request (400 Bad Request)
- Failed to fetch PDF (500 Internal Server Error)
- Invalid printer configuration (500 Internal Server Error)
- Missing required headers (500 Internal Server Error)
- Authentication failures (500 Internal Server Error)

## Development

### Prerequisites

- Home Assistant development environment
- Python 3.8 or higher
- CUPS server for testing

### Building

1. Clone the repository
```bash
git clone https://github.com/arest/cups-client-addon
```

2. Build the add-on
```bash
docker build -t cups-client-addon .
```

### Testing

To test the add-on locally:

1. Ensure you have a CUPS server running
2. Configure the add-on with your CUPS server details
3. Send a test print job using either:
   - The Home Assistant service
   - The direct API endpoint

## Support

If you encounter any issues or have questions:

1. Check the [Issues](https://github.com/arest/cups-client-addon/issues) page
2. Create a new issue if your problem isn't already reported

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
