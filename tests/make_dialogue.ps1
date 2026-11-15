# Generates a SYNTHETIC two-voice consultation (Windows TTS) for testing. No real patient data.
param([string]$OutDir)
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(48000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$lines = @(
  @("Microsoft David Desktop", "Good morning. Please have a seat. What brings you in today?"),
  @("Microsoft Zira Desktop",  "Good morning doctor. I have had a headache and a mild fever for three days."),
  @("Microsoft David Desktop", "Have you taken any medicine for the fever so far?"),
  @("Microsoft Zira Desktop",  "Only paracetamol, twice a day. It helps for a few hours."),
  @("Microsoft David Desktop", "Alright. Let me check your temperature and blood pressure."),
  @("Microsoft Zira Desktop",  "Sure. I also feel tired in the evenings."),
  @("Microsoft David Desktop", "Your blood pressure is normal. Drink plenty of water and rest well."),
  @("Microsoft Zira Desktop",  "Thank you doctor. Should I come back next week?"),
  @("Microsoft David Desktop", "Yes, come back on Monday if the fever continues.")
)
$i = 0
foreach ($l in $lines) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice($l[0]); $s.Rate = -1
  $s.SetOutputToWaveFile((Join-Path $OutDir ("line{0:D2}.wav" -f $i)), $fmt)
  $s.Speak($l[1]); $s.Dispose(); $i++
}
