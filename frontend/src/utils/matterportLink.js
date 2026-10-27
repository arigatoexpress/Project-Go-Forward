// Staff-entered Matterport tours. The public page only embeds an id that
// matches this shape, so the link we show staff is rebuilt the same way.

const MATTERPORT_ID_RE = /^[A-Za-z0-9]{11}$/;

export function canonicalMatterportId(value) {
  const id = String(value ?? '').trim();
  return MATTERPORT_ID_RE.test(id) ? id : '';
}

export function canonicalMatterportUrl(value) {
  const id = canonicalMatterportId(value);
  if (!id) return '';
  return `https://my.matterport.com/show/?m=${id}&play=1`;
}
