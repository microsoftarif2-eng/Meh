import os

root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__)))
folder = os.path.join(root_dir, "data", "extractedwavs", "voice_type")
# D:\CodeProjects\VoiceForge\data\extractedwavs\femaleeventoned
# D:\CodeProjects\VoiceForge\data\extractedwavs\voice_type
print("Root dir:", root_dir)
print("Target folder:", folder)
