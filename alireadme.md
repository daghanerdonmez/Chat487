# Applied Computer Networks - Workshop 4

In this workshop, we added UDP-based file transfer with custom flow control to the P2P LAN chat application.

_NOTE: I tested my code with Daghan Erdonmez's._

## How to run the code

No external dependencies are needed (pure Python stdlib). To run:

```bash
$ python3 main.py
```

## Commands

- `/users` - List discovered users on the network
- `/sendfile` - Send a file to another user (prompts for username and file path)
- `/help` - Show available commands
- `/exit` - Exit the application

## File transfer

File transfer uses UDP with a custom flow control mechanism:

- Files are split into 1500-byte chunks
- Each chunk is base62-encoded and sent as a JSON packet over UDP
- The receiver maintains a 10-packet buffer and sends ACKs with a receive window (RWND)
- The sender respects RWND to avoid overwhelming the receiver
- Packets not ACKed within 1 second are retransmitted
- An `EOF` field marks the final packet
