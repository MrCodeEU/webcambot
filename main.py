import discord
from discord.ext import commands
import aiohttp
import io
import os
import tempfile
import asyncio
from datetime import datetime
from dotenv import load_dotenv
import subprocess
import re

# Bot Version Info
BOT_VERSION = "1.0.0"
BOT_LAST_UPDATED = "Not deployed yet"  # This will be replaced during deployment

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
HA_URL = os.getenv('HA_URL')
HA_TOKEN = os.getenv('HA_TOKEN')
CAMERA_ENTITY_ID = os.getenv('CAMERA_ENTITY_ID')

# Set up Discord bot with minimal intents and custom help command
intents = discord.Intents.default()
intents.message_content = True

# Create custom help command class
class CustomHelpCommand(commands.DefaultHelpCommand):
    def __init__(self):
        super().__init__(
            no_category="Commands",
            sort_commands=True,
            width=2000,  # Wider output
        )

    async def send_command_help(self, command):
        """Sends help for a specific command."""
        embed = discord.Embed(
            title=f"Help: {command.name}",
            description=command.help or "No description available.",
            color=discord.Color.blue()
        )

        # Add usage
        usage = self.get_command_signature(command)
        embed.add_field(name="Usage", value=f"```{usage}```", inline=False)

        # Add examples if they exist
        if hasattr(command, 'examples'):
            examples = '\n'.join(command.examples)
            embed.add_field(name="Examples", value=f"```{examples}```", inline=False)

        channel = self.get_destination()
        await channel.send(embed=embed)

    async def send_bot_help(self, mapping):
        """Sends the bot's help message."""
        embed = discord.Embed(
            title="📷 Home Assistant Camera Bot Help",
            description="Here are all available commands:",
            color=discord.Color.blue()
        )

        # Add a field for each command
        for command in self.context.bot.commands:
            # Skip hidden commands
            if command.hidden:
                continue

            # Create command description
            description = command.help or "No description available."
            usage = self.get_command_signature(command)

            value = f"Description: {description}\nUsage: `{usage}`"

            # Add examples if they exist
            if hasattr(command, 'examples'):
                examples = '\n'.join(f"• {example}" for example in command.examples)
                value += f"\nExamples:\n{examples}"

            embed.add_field(
                name=f"!{command.name}",
                value=value,
                inline=False
            )

        # Add footer with additional info
        embed.set_footer(text="Type !help <command> for more detailed information about a specific command.")

        channel = self.get_destination()
        await channel.send(embed=embed)

# Initialize bot with custom help command
bot = commands.Bot(
    command_prefix='!',
    intents=intents,
    help_command=CustomHelpCommand()
)

async def get_camera_image():
    """Fetch camera image from Home Assistant."""
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "content-type": "application/json",
    }

    camera_url = f"{HA_URL}/api/camera_proxy/{CAMERA_ENTITY_ID}"

    async with aiohttp.ClientSession() as session:
        async with session.get(camera_url, headers=headers) as response:
            if response.status == 200:
                return await response.read()
            else:
                raise Exception(f"Failed to get camera image. Status: {response.status}")

async def get_camera_stream_url():
    """Get the HLS stream URL from Home Assistant."""
    headers = {
        "Authorization": f"Bearer {HA_TOKEN}",
        "content-type": "application/json",
    }

    camera_url = f"{HA_URL}/api/camera_proxy_stream/{CAMERA_ENTITY_ID}"

    async with aiohttp.ClientSession() as session:
        async with session.get(camera_url, headers=headers) as response:
            if response.status == 200:
                return camera_url
            else:
                raise Exception(f"Failed to get camera stream. Status: {response.status}")

async def record_video(duration):
    """
    Record video from the camera stream using direct stream copy.
    """
    output_path = None
    try:
        if not (1 <= duration <= 60):
            raise ValueError("Duration must be between 1 and 60 seconds")

        stream_url = await get_camera_stream_url()

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
            output_path = temp_file.name

        # Construct FFmpeg command with proper stream handling
        headers = f"Authorization: Bearer {HA_TOKEN}"
        
        command = [
            'ffmpeg',
            '-y',
            '-headers', headers,
            '-i', stream_url,
            '-t', str(duration),
            '-c:v', 'copy',  # Copy video stream
            '-c:a', 'copy',  # Copy audio stream if exists
            '-avoid_negative_ts', 'make_zero',
            '-fflags', '+genpts',  # Generate presentation timestamps
            '-movflags', '+faststart',
            output_path
        ]

        # Start the FFmpeg process
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        try:
            # Add extra time to account for stream initialization
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=duration + 15
            )
        except asyncio.TimeoutError:
            if process:
                process.kill()
            raise Exception("Recording timed out")

        if process.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            raise Exception(f"Failed to record video: {error_msg}")

        # Verify the output file
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:  # Less than 1KB
            raise Exception("Output file is too small or missing")

        # Wait a moment to ensure file is properly written
        await asyncio.sleep(1)
        
        return output_path

    except Exception as e:
        if output_path and os.path.exists(output_path):
            try:
                os.unlink(output_path)
            except:
                pass
        raise e

@bot.event
async def on_ready():
    """Called when the bot is ready."""
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')

    # Set bot status
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="your cameras 📷 | !help"
        )
    )

@bot.command(
    name='hastatus',
    help='Check if the bot can connect to your Home Assistant instance.',
    brief='Check Home Assistant connection'
)
async def hastatus(ctx):
    """Check if Home Assistant is reachable and show connection details."""
    try:
        headers = {
            "Authorization": f"Bearer {HA_TOKEN}",
            "content-type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{HA_URL}/api/", headers=headers) as response:
                if response.status == 200:
                    embed = discord.Embed(
                        title="Home Assistant Status",
                        color=discord.Color.green()
                    )
                    embed.add_field(
                        name="Status",
                        value="✅ Connected",
                        inline=False
                    )
                    embed.add_field(
                        name="URL",
                        value=f"`{HA_URL}`",
                        inline=True
                    )
                    embed.add_field(
                        name="Camera Entity",
                        value=f"`{CAMERA_ENTITY_ID}`",
                        inline=True
                    )
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(f"❌ Could not connect to Home Assistant. Status code: {response.status}")
    except Exception as e:
        await ctx.send(f"❌ Error connecting to Home Assistant: {str(e)}")

@bot.command(
    name='webcam',
    help='Capture and send a current image from the camera.',
    brief='Get current camera image',
    examples=[
        "!webcam  # Gets current image"
    ]
)
async def webcam(ctx):
    """Command to fetch and send the webcam image."""
    msg = None
    try:
        # Get the image first
        image_data = await get_camera_image()

        # Create the file object
        image_file = discord.File(
            io.BytesIO(image_data),
            filename=f'camera_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg'
        )

        # Send a single message with the image
        await ctx.send("📷 Here's your image:", file=image_file)

    except Exception as e:
        await ctx.send(f"❌ Error capturing image: {str(e)}")
        if msg:
            try:
                await msg.delete()
            except:
                pass

        raise e

@bot.command(
    name='record',
    help='Record a video clip from the camera',
    brief='Record video clip',
    enabled=True,
    examples=[
        "!record 5  # Record 5 seconds",
        "!record 30  # Record 30 seconds"
    ]
)
async def record_command(ctx, duration: int):
    """Command to record and send a video clip."""
    processing_msg = None
    video_path = None
    try:
        if not (1 <= duration <= 60):
            await ctx.send("⚠️ Duration must be between 1 and 60 seconds!")
            return

        processing_msg = await ctx.send(f"🎥 Starting {duration}-second recording...")
        
        if duration > 5:
            for i in range(0, duration, 5):
                await asyncio.sleep(5)
                await processing_msg.edit(content=f"🎥 Recording in progress... {i+5}/{duration}s")

        video_path = await record_video(duration)
        await processing_msg.edit(content="📼 Processing video...")
        
        # Check if the video exists and has content
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            await ctx.send("❌ Error: Failed to record video (empty file created)")
            return
            
        # Get file size
        file_size = os.path.getsize(video_path) / (1024 * 1024)  # Convert to MB
        
        # Check if file size is within Discord's limit (8MB for small servers)
        if file_size > 8:
            await ctx.send("⚠️ Recording was successful but the file is too large to send (>8MB). Try a shorter duration.")
            return
            
        with open(video_path, 'rb') as video_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            discord_file = discord.File(
                video_file,
                filename=f'camera_clip_{timestamp}.mp4'
            )
            await ctx.send(
                content=f"📹 Here's your {duration}-second video clip (Size: {file_size:.1f}MB):",
                file=discord_file
            )
    except Exception as e:
        await ctx.send(f"❌ Error recording video: {str(e)}")
        raise e
    finally:
        if video_path and os.path.exists(video_path):
            try:
                os.unlink(video_path)
            except:
                pass
        if processing_msg:
            try:
                await processing_msg.delete()
            except:
                pass

@bot.command(
    name='about',
    help='Show information about the bot.',
    brief='Show bot info'
)
async def about(ctx):
    """Show information about the bot."""
    embed = discord.Embed(
        title="📷 Home Assistant Camera Bot",
        description="A Discord bot for interacting with Home Assistant cameras.",
        color=discord.Color.blue()
    )

    # Add bot information
    embed.add_field(
        name="Version Info",
        value=f"Version: `{BOT_VERSION}`\nLast Updated: `{BOT_LAST_UPDATED}`",
        inline=False
    )

    embed.add_field(
        name="Commands",
        value="Type `!help` to see all available commands",
        inline=False
    )

    embed.add_field(
        name="Features",
        value="• Capture images\n• Record video clips\n• Check HA status",
        inline=False
    )

    await ctx.send(embed=embed)

@bot.event
async def on_command_error(ctx, error):
    """Global error handler"""
    if isinstance(error, commands.errors.CheckFailure):
        await ctx.send('⛔ You do not have permission to use this command.')
    elif isinstance(error, commands.errors.CommandNotFound):
        pass  # Ignore command not found errors
    elif isinstance(error, commands.errors.MissingRequiredArgument):
        if ctx.command.name == 'record':
            await ctx.send('⚠️ Please specify the duration in seconds (1-60). Example: `!record 10`')
        else:
            await ctx.send(f'⚠️ Missing required argument: {str(error)}')
    else:
        await ctx.send(f'❌ An error occurred: {str(error)}')

def main():
    """Main function to run the bot"""
    if not DISCORD_TOKEN:
        raise ValueError("No Discord token found. Make sure DISCORD_TOKEN is set in your .env file.")
    if not all([HA_URL, HA_TOKEN, CAMERA_ENTITY_ID]):
        raise ValueError("Missing required Home Assistant configuration in .env file.")

    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    main()