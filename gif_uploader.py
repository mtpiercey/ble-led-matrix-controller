#!/usr/bin/env python3

'''
 * GIF Upload Script for Flexible 96x20 LED Matrix
 * Copyright (c) 2025 Matthew Piercey
 *
 * https://github.com/mtpiercey/ble-led-matrix-controller
 *
 * MIT LICENSE:
 * Permission is hereby granted, free of charge, to any person obtaining a copy of
 * this software and associated documentation files (the "Software"), to deal in
 * the Software without restriction, including without limitation the rights to
 * use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
 * the Software, and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
 * FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
 * COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
 * IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 * CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 *
 * Instructions:
 * python gif_uploader.py somefile.gif
 * - Where somefile.gif is the name of the GIF animation you wish to upload to the LED matrix
 * - somefile.gif must be under 49,980 bytes (~48 KiB)
 * - The screen will attempt to render it regardless, but it works best with animations that are 96x20 pixels
 * - Ensure your computer is capable of connecting to BLE devices
 * - Also ensure you have installed the bleak and tqdm modules as dependencies
'''

from bleak import BleakClient
from tqdm import tqdm

import argparse
import asyncio
import sys

# The screen will likely have this BLE address. Ensure it is not paired to any other device before running this script.
# Using an app like nRF Connect can help you find the addresses of all BLE devices in your vicinity.
DEVICE_ADDRESS = "ff:24:06:18:41:5f"

# For whatever reason, this maps to the right BLE handle for binary data sending, although Wireshark sees it as 0x000e
HANDLE = 0x000d

# The second-to-last byte is a CheckSum8 Modulo 256 of the preceding bytes (see https://www.scadacore.com/tools/programming-calculators/online-checksum-calculator/)
def checksum_mod256(hex_string):
	return hex(sum(int(hex_string[i:i+2], 16) for i in range(0, len(hex_string), 2)) % 256).upper()[2:].zfill(2)

# The last byte requires a specific checksum calculation, taking into account all previous bytes
def calculate_last_byte(hex_string):
	# Convert the preceding bytes to an array of integers and take their sum
	data_bytes = [int(hex_string[i:i+2], 16) for i in range(0, len(hex_string) - 2, 2)]
	total_sum = sum(data_bytes)

	# The last byte is the high byte of the total sum
	# TODO: Can this and checksum_mod256 be combined somehow?
	last_byte = total_sum // 256
	return f"{last_byte:02X}"

# This sequence ensures the screen is ready for a new animation to be uploaded
async def reset_screen():
	# Sending this byte array tells the screen to delete whatever animation(s) it was currently storing, so they can be overwritten
	# Technically the screen has the capability to store multiple animations and swap between them, but that went beyond the scope of this proof-of-concept
	await client.write_gatt_char(HANDLE, bytearray.fromhex("aa55ffff0a000900c102080200ffdc04"), response=False)
	await asyncio.sleep(0.5)
	# Sending this gets the screen ready to receive a new animation
	await client.write_gatt_char(HANDLE, bytearray.fromhex("aa55ffff0a000900c10208020000dd03"), response=False)
	await asyncio.sleep(0.5)

def generate_header(payload, index, animation_length):
	# The header always starts with this
	header = "aa55ffff"

	# This is the byte length, in hex, from the first page number below, until the second-to-last byte
	# (including the first checksum byte but not the last)
	# Hex byte length of packet plus 41 (40 bytes preceding, 1 byte trailing included in length calculation)
	header += hex(int(len(payload)/ 2) + 41)[2:]

	# Page/packet number
	# 000000, 000100, 000200, 000300, 000400, 000500, 000600, 000700, 000800, 000900, 000a00, 000b00, etc.
	header += f"{index:04x}00"

	# Always constant
	header += "c1020901010c01000d01000e0100140301090a11040001000a1207"

	# The length of the GIF, in frames, in hex - 0c means 12
	header += f"{animation_length:02X}"

	# Page number again
	# 000000, 000100, 000200, 000300, 000400, 000500, 000600, 000700, 000800, 000900, 000a00, 000b00, etc.
	header += f"{index:04x}00"

	# Seems to always be constant, not sure what it represents
	header += "c4000013"

	# TODO: Figure out how to calculate this byte sequence
	# Has something to do with the length of the payload (81c4 for a full payload, but lower if the payload isn't a full 196 bytes)
	# For now, file_to_hex_chunks is just padding the last payload with 0's, so these bytes will work
	header += "81c4"
	return header

# Given a binary payload, the payload index, and the length of the animation (in number of packets)
# Generate a packet (including a header and four-byte checksum trailer)
def generate_packet(payload, index, animation_length):
	# Header
	header = generate_header(payload, index, animation_length)
	full_value = header + payload

	# First two bytes of the checksum trailer
	checksum = checksum_mod256(full_value)
	full_value = full_value + checksum

	# Last two bytes of the checksum trailer
	last_byte = calculate_last_byte(full_value)
	full_value = full_value + last_byte

	return full_value

# Split a GIF file into chunks
def file_to_hex_chunks(filename, chunk_size=392):
	try:
		with open(filename, "rb") as file:
			hex_string = file.read().hex()
	except:
		print("Unable to open GIF file")
		sys.exit(1)
	
	# Naive GIF file validation
	if not (hex_string.startswith("GIF87a".encode("ascii").hex()) or hex_string.startswith("GIF89a".encode("ascii").hex())):
		print(f"{filename} is not a valid GIF file.\n")
		sys.exit(1)

	hex_chunks = [
		# TODO: The padding with 0's is currently necessary because the pre-packet byte sequence pattern isn't clear
		# So at least we can add some 0's, fill up the last packet so it's 196 bytes, and use the default "81c4" value in generate_header
		hex_string[i:i + chunk_size].ljust(chunk_size, '0')
		for i in range(0, len(hex_string), chunk_size)
	]

	# TODO: Not sure if this is a hard limit, but it appears to be given how the length in number of packets seems to be a two-digit hex value
	if len(hex_chunks) > 255:
		print("Please select a smaller GIF file (under 49,980 bytes or ~48KiB)\n")
		sys.exit(1)

	return hex_chunks

# Naive BLE notification handling logic
# Basically just wait for any notification to come in from the device, to trigger the asyncio event
notification_event = asyncio.Event()

def notification_handler(sender, data):
	notification_event.set()

async def main():
	global client

	# Get the name of the GIF file to process from the CLI arguments (should be after the name of the command)
	parser = argparse.ArgumentParser(description="Script to upload a GIF to a flexible 96x20 LED matrix")
	parser.add_argument("gif", type=str, help="The name of the GIF file you wish to upload")
	args = parser.parse_args()

	GIF_FILE_NAME = args.gif
	hex_chunks = file_to_hex_chunks(GIF_FILE_NAME)

	async with BleakClient(DEVICE_ADDRESS) as client:
		if client.is_connected:
			try:
				# Start receiving indications
				INDICATIONS_UUID="00002a05-0000-1000-8000-00805f9b34fb"
				await client.start_notify(INDICATIONS_UUID, notification_handler)
			except Exception as e:
				print(f"Failed to enable indications: {e}")
				sys.exit(1)

			try:
				# Start receiving notifications
				NOTIFICATIONS_UUID="0000fff1-0000-1000-8000-00805f9b34fb"
				await client.start_notify(NOTIFICATIONS_UUID, notification_handler)
			except Exception as e:
				print(f"Failed to enable notifications: {e}")
				sys.exit(1)

			await reset_screen()
		
			print(f"Connected to {DEVICE_ADDRESS}")
			print(f"Uploading {GIF_FILE_NAME} (~{len(hex_chunks) * 196} bytes)...\n")

			progress_bar = tqdm(total=len(hex_chunks), desc="Progress", unit=" Packets")

			packet_index = 0

			for hex_chunk in hex_chunks:
				packet = ""
				notification_event.clear()

				try:
					# Generate the binary packet to upload
					packet = generate_packet(hex_chunk, packet_index, len(hex_chunks))

					# Upload the packet to the screen
					await client.write_gatt_char(HANDLE, bytearray.fromhex(packet), response=False)

					# Naively wait for any notification, but it's likely that the notification will be because the current packet was received
					# TODO: It may be possible to upload more than one packet at a time, and check latest value received notitications so as to not overflow the screen's input buffer
					# For now, this system works even if it may not be as fast as possible
					await asyncio.wait_for(notification_event.wait(), timeout=0.75)

					progress_bar.update(1)
				except Exception as e:
					print(e)
					print("An upload error occurred!")
					sys.exit(1)
				
				packet_index += 1
			
			progress_bar.close()
			
			# Not really sure what this does (or why it's sent twice), but seems to indicate the the screen that the upload has finished
			await client.write_gatt_char(HANDLE, bytearray.fromhex("aa55ffff0b000f00c10236030100001404"), response=False)
			await client.write_gatt_char(HANDLE, bytearray.fromhex("aa55ffff0b000f00c10236030100001404"), response=False)

			print("\nUpload successful!")

		else:
			print("Failed to connect to the device")
			sys.exit(1)

asyncio.run(main())
