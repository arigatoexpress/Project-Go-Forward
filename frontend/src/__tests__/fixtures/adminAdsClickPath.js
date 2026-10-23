// Shared fixtures for the admin, listing, Ads, and AI Ads Studio click path.
// The PIN is a test string. It is not a store secret and is accepted only by mocks.

export const FIXTURE_PIN = 'clickpath-fixture-pin';

export const FIXTURE_HOME = {
  id: 'clickpath-single',
  model_name: 'Clickpath Single',
  manufacturer: 'Fixture Maker',
  classification: 'Single Wide',
  status: 'AVAILABLE',
  is_new: true,
  beds: 3,
  baths: 2,
  sqft: 980,
  width: 16,
  length: 60,
  features: ['Porch'],
  public_sale_price: null,
  sale_price: null,
};

export const STUDIO_HOME = {
  id: 'clickpath-single',
  model_name: 'Clickpath Single',
  manufacturer: 'Fixture Maker',
  status: 'Available',
  display_price: 'Call for Price',
  specs: { beds: 3, baths: 2, sq_ft: 980 },
  real_photos: [],
};

export function internalDraftStatus() {
  const digest = 'a'.repeat(64);
  return {
    schema_version: 3,
    deployment_id: `tho-search-high-intent-huffman-v1--${digest}`,
    deployment_key: 'tho-search-high-intent-huffman-v1',
    contract_hash: `sha256:${digest}`,
    state: 'INTERNAL_DRAFT',
    state_source: 'FIRESTORE_AUTHORITY_LEDGER',
    version: 1,
    updated_at: '2026-08-12T12:00:00Z',
    feature_enabled: false,
    ready: false,
    spend_enabled: false,
    budget: {
      average_daily_usd: 20,
      max_single_day_charge_usd: 40,
      monthly_charge_limit_usd: 608,
      max_cpc_usd: 5,
    },
    campaign_review: {
      campaign_name: 'THO | Search | High Intent | Huffman | Draft',
      status: 'PAUSED',
      channel: 'SEARCH',
      currency_code: 'USD',
      bidding: { strategy: 'MAXIMIZE_CLICKS', max_cpc_usd: 5 },
      networks: { google_search: true, search_partners: false, display: false },
      geo: {
        type: 'RADIUS',
        center: {
          address: '10685 FM 1960 East',
          city: 'Huffman',
          state: 'TX',
          country_code: 'US',
          latitude: 30.018056,
          longitude: -95.115729,
        },
        radius_miles: 50,
        presence_only: true,
        postal_codes: [],
      },
      housing_policy: {
        acknowledgement_required: true,
        acknowledged: false,
        age_enabled_all: true,
        gender_enabled_all: true,
        parental_status_enabled_all: true,
        marital_status_targeting: false,
        postal_code_targeting: false,
        demographic_exclusions: [],
        customer_match_targeting: false,
        audience_targeting: [],
      },
      tracking: {
        utm_source: 'google',
        utm_medium: 'cpc',
        utm_campaign: 'tho_search_high_intent_huffman',
        utm_content: '{creative}',
        utm_term: '{keyword}',
      },
      negative_keywords: Array.from({ length: 16 }, (_, index) => ({
        text: index === 0 ? 'rent' : `negative ${index}`,
        match_type: index % 2 ? 'PHRASE' : 'BROAD',
      })),
      ad_groups: [
        {
          name: 'Local Inventory',
          status: 'PAUSED',
          keywords: Array.from({ length: 11 }, (_, index) => ({
            text: index === 0 ? 'manufactured homes huffman tx' : `inventory keyword ${index}`,
            match_type: index % 2 ? 'PHRASE' : 'EXACT',
          })),
          responsive_search_ad: {
            status: 'PAUSED',
            final_url: 'https://www.texashomeoutlet.com/inventory',
            path1: 'homes',
            path2: 'huffman',
            headlines: ['Texas Home Outlet', 'Browse Homes With Prices', 'Explore Home Models'],
            descriptions: [
              'Browse manufactured home models with prices, photos and floor plans near Huffman.',
              'Review home details online and request a showroom tour.',
            ],
          },
        },
        {
          name: 'Showroom Tours',
          status: 'PAUSED',
          keywords: Array.from({ length: 7 }, (_, index) => ({
            text: index === 0 ? 'manufactured home showroom' : `showroom keyword ${index}`,
            match_type: index % 2 ? 'PHRASE' : 'EXACT',
          })),
          responsive_search_ad: {
            status: 'PAUSED',
            final_url: 'https://www.texashomeoutlet.com/appointments',
            path1: 'showroom',
            path2: 'tour',
            headlines: ['Tour Manufactured Homes', 'Book a Showroom Visit', 'Choose Your Tour Time'],
            descriptions: [
              'Pick an available time online and tour manufactured homes at our Huffman showroom.',
              'Browse prices and floor plans first, then schedule a visit.',
            ],
          },
        },
      ],
      conversion_intent: {
        provider_goal_operations_in_paused_create: false,
        import_required_before_activation: true,
        activation_hold_check: 'google_ads_conversion_import_verified',
        events: [
          { name: 'schedule_appointment', primary: true },
          { name: 'generate_lead', primary: false },
        ],
      },
      stop_loss: {
        action: 'PAUSE_CAMPAIGN',
        decision_logic: 'ANY',
        evaluation_window_days: 7,
        zero_reachable_leads_spend_usd: 200,
        zero_reachable_leads_clicks: 100,
        max_reachable_lead_cpa_usd: 150,
        minimum_reachable_leads_for_cpa_rule: 3,
      },
      activation_supported: false,
      spend_authorized: false,
    },
    workflow: [
      { state: 'INTERNAL_DRAFT', status: 'current' },
      { state: 'SERVER_VALIDATED', status: 'not_started' },
      { state: 'PAUSED_CREATE_APPROVED', status: 'not_started' },
      { state: 'PAUSED_CREATED', status: 'not_started' },
    ],
    actions: { server_validation: true },
    paused_create: {
      outbox_state: null,
      activation_authorized: false,
      spend_enabled: false,
    },
    events: {
      count: 1,
      first_version: 1,
      items: [{
        event_id: '00000000000000000001-internal-draft-created',
        event_type: 'INTERNAL_DRAFT_CREATED',
        record_version: 1,
        from_state: null,
        to_state: 'INTERNAL_DRAFT',
        error_code: null,
        occurred_at: '2026-08-12T12:00:00Z',
      }],
    },
  };
}
