// Build the Radxa appliance UI without upstream transcription/cloud entrypoints.
// Normal Scriberr builds preserve all upstream routes and navigation.
export const meetingStationMode = import.meta.env.VITE_MEETING_STATION === 'true'
