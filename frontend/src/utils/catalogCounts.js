// Public catalog counts. The API sets inventory_kind with the same three
// buckets (available_now, pre_owned, orderable_floorplan) and reports them
// on catalog_summary. The page counts those kinds on the homes it is
// showing. It does not add a second total that mixes buckets.

export function catalogKind(home) {
  const raw = String(home?.inventory_kind || '').trim();
  if (raw) return raw;
  const status = String(home?.status || '').trim();
  if (status === 'Orderable') return 'orderable_floorplan';
  if (/pre/i.test(status) && /owned/i.test(status)) return 'pre_owned';
  return 'available_now';
}

export function countCatalog(homes) {
  const counts = { availableNow: 0, preOwned: 0, orderable: 0, other: 0, tours: 0 };
  for (const home of homes || []) {
    const kind = catalogKind(home);
    if (kind === 'available_now') counts.availableNow += 1;
    else if (kind === 'pre_owned') counts.preOwned += 1;
    else if (kind === 'orderable_floorplan') counts.orderable += 1;
    else counts.other += 1;
    if (home?.matterport_id) counts.tours += 1;
  }
  return counts;
}

function joinAnd(parts) {
  if (parts.length <= 1) return parts[0] || '';
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return `${parts.slice(0, -1).join(', ')}, and ${parts[parts.length - 1]}`;
}

function labeled(count, singular, plural) {
  return `${count} ${count === 1 ? singular : plural}`;
}

// One sentence for the same counts the tiles show. "On the lot" is
// available_now only. Pre-owned homes are their own bucket. Orderable
// floorplans are manufacturer plans, not homes sitting on the lot.
export function formatCatalogCounts(counts, { scope = 'catalog' } = {}) {
  const parts = [
    labeled(counts.availableNow, 'on the lot', 'on the lot'),
    labeled(counts.preOwned, 'pre-owned', 'pre-owned'),
    labeled(counts.orderable, 'orderable floorplan', 'orderable floorplans'),
  ];
  if (counts.other > 0) parts.push(labeled(counts.other, 'other listing', 'other listings'));
  const line = joinAnd(parts);
  if (scope === 'group') return `In this group: ${line}.`;
  return `${line}.`;
}
