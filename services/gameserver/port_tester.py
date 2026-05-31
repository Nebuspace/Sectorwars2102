#!/usr/bin/env python3
"""
Station testing utility for Replit environment
Tests various ports to identify which ones can be bound to successfully
"""
import socket
import sys
import os
import time
from threading import Thread

def test_port(port, host="127.0.0.1", duration=1):
    """Test if we can bind and listen on a port for a specified duration.

    Host defaults to loopback only. Callers that genuinely need to bind on all
    interfaces (e.g., in-container reachability probes) can pass host="0.0.0.0"
    explicitly so the security-scanner finding is acknowledged at the call site.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(1)
        print(f"✅ Successfully bound to port {port}")

        # Hold the port open briefly
        time.sleep(duration)
        s.close()
        return True
    except Exception as e:
        print(f"❌ Could not bind to port {port}: {e}")
        return False

def serve_http_response(port, host="127.0.0.1", text="Station test successful"):
    """Try to serve a simple HTTP response on the port. Loopback-only by default."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        s.listen(1)

        print(f"🌐 HTTP server running on port {port}...")
        print(f"Try accessing: http://localhost:{port}")
        print("Waiting for connection...")

        conn, addr = s.accept()
        print(f"Connection from: {addr}")

        # Send a simple HTTP response
        content = f"<html><body><h1>{text} on port {port}</h1><p>Server time: {time.ctime()}</p></body></html>"
        headers = [
            "HTTP/1.1 200 OK",
            f"Content-Length: {len(content)}",
            "Content-Type: text/html",
            "Connection: close",
            "",
            ""
        ]
        response = "\r\n".join(headers) + content
        conn.send(response.encode())
        conn.close()
        s.close()

        print(f"✅ Successfully served HTTP response on port {port}")
        return True
    except Exception as e:
        print(f"❌ Error serving on port {port}: {e}")
        return False

def test_common_ports():
    """Test a range of commonly used ports"""
    common_ports = [
        3000,  # Node.js/React
        5000,  # Flask/Python
        8000,  # Django/Python
        8080,  # Alternative HTTP
        8888,  # Jupyter
        9000,  # Common alternative
        4000,  # Common alternative
        1337,  # Dev port
    ]

    results = {}

    print("Testing common ports...")
    for port in common_ports:
        result = test_port(port)
        results[port] = result

    print("\nResults summary:")
    for port, success in results.items():
        status = "✅ Available" if success else "❌ Not available"
        print(f"Station {port}: {status}")

    # Return the list of successful ports
    return [port for port, success in results.items() if success]

def test_port_range(start=8000, end=9000):
    """Test a range of ports to find available ones"""
    available_ports = []

    print(f"Testing port range {start}-{end}...")
    for port in range(start, end+1):
        if test_port(port, duration=0.1):
            available_ports.append(port)

    print(f"\nFound {len(available_ports)} available ports")
    if available_ports:
        print(f"Available ports: {available_ports[:10]}...")

    return available_ports

def run_http_server(port):
    """Run a simple HTTP server on the specified port"""
    print(f"Starting HTTP server on port {port}")
    serve_http_response(port)

if __name__ == "__main__":
    print("Replit Station Tester")
    print("=================")
    print(f"Python version: {sys.version}")
    print(f"Current directory: {os.getcwd()}")
    print(f"Environment: {os.environ.get('ENVIRONMENT', 'Not set')}")
    print()

    # Parse command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()

        if command == "testall":
            # Test all common ports
            available_ports = test_common_ports()

            if available_ports:
                print("\nRecommended port to use:", available_ports[0])
            else:
                print("\nNo common ports available!")

        elif command == "scan":
            # Scan for available ports in a range
            start = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
            end = int(sys.argv[3]) if len(sys.argv) > 3 else 8100
            test_port_range(start, end)

        elif command == "serve":
            # Start a simple HTTP server
            port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
            run_http_server(port)

        elif command == "test":
            # Test a specific port
            port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
            test_port(port)

        else:
            print("Unknown command!")
    else:
        # Default: test important ports
        print("Testing important ports for Sector Wars...")
        test_port(3000)  # Player Client
        test_port(3001)  # Admin UI
        test_port(5000)  # API Server (original)
        test_port(8080)  # API Server (new)

        print("\nTo run a simple HTTP server on port 8080:")
        print("python3 port_tester.py serve 8080")

        print("\nTo scan a range of ports:")
        print("python3 port_tester.py scan 8000 8100")

        print("\nTo test all common ports:")
        print("python3 port_tester.py testall")
