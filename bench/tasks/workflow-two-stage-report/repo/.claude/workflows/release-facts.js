export const meta = {
  name: 'release-facts',
  description: 'Look up the version and the upload limit, then write REPORT.json from them',
  phases: [
    { title: 'Look up', detail: 'find the two facts in the code' },
    { title: 'Write', detail: 'write REPORT.json' },
  ],
}

const FACTS = {
  type: 'object',
  properties: { version: { type: 'string' }, max_upload_mb: { type: 'integer' } },
  required: ['version', 'max_upload_mb'],
}

phase('Look up')
const facts = await agent(
  'Read this repository and find two facts as its code ships them, with no environment variables set: the version ' +
    'the package reports, and the largest upload it accepts, in megabytes. Change no file.',
  { label: 'lookup', schema: FACTS },
)

phase('Write')
return await agent(
  'Write REPORT.json at the root of this repository holding exactly this JSON object, and change no other file: ' +
    JSON.stringify({ version: facts.version, max_upload_mb: facts.max_upload_mb }) +
    '. Reply with the file as written.',
  { label: 'write' },
)
