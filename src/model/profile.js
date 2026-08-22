// UserBaselineProfile — the input contract read by the anomaly-detection module
// and the AI-agent call logic in later build passes.
//
// This file is pure data logic: no UI, no sensors, no storage. Later modules
// should import from here (and storage/profileStore) and nothing else.
//
// Shape produced by buildProfile():
// {
//   schemaVersion: 1,
//   demographics: { name, sex, dob: 'YYYY-MM-DD', livingSituation },
//   emergencyContacts: [{ name, relationship, phone, isPrimary }],   // primary first
//   sleep: { typicalWake: 'HH:MM', typicalSleep: 'HH:MM', napPattern: string|null },
//   weeklyRoutine: { monday..sunday: [{ activity, expectedTime: 'HH:MM', expectedDuration: minutes }] },
//   hobbies: [string],
//   mobilityLevel: 'fully_mobile' | 'walking_aid' | 'limited_mobility',
//   lifestyle: { diet: string|null, smoking: { status, frequency|null }, alcohol: { status, frequency|null } },
//   healthContext: [string],        // self-reported, advisory only — never diagnostic
//   medicationCount: number|null,
//   consent: { monitoringConsent: bool, sharedWith: [string] },
//   deviceId: null,                 // reserved for wearable pairing
//   completedAt: ISO datetime
// }

import { parseTime, parseDob, formatTime, formatDob, normalizePhone } from '../utils/datetime';

export const PROFILE_SCHEMA_VERSION = 1;

export const SEX_OPTIONS = [
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
  { value: 'other', label: 'Other' },
  { value: 'prefer_not_to_say', label: 'Prefer not to say' },
];

export const LIVING_SITUATION_OPTIONS = [
  { value: 'lives_alone', label: 'Lives alone' },
  { value: 'lives_with_someone', label: 'Lives with someone' },
  { value: 'carer_visits', label: 'Carer visits regularly' },
];

export const MOBILITY_OPTIONS = [
  { value: 'fully_mobile', label: 'Fully mobile' },
  { value: 'walking_aid', label: 'Uses a walking aid' },
  { value: 'limited_mobility', label: 'Limited mobility' },
];

export const DIET_OPTIONS = [
  { value: 'no_restrictions', label: 'No restrictions' },
  { value: 'vegetarian', label: 'Vegetarian' },
  { value: 'vegan', label: 'Vegan' },
  { value: 'diabetic_friendly', label: 'Diabetic-friendly' },
  { value: 'low_salt', label: 'Low salt' },
  { value: 'other', label: 'Other' },
];

export const FREQUENCY_OPTIONS = [
  { value: 'occasionally', label: 'Occasionally' },
  { value: 'few_times_week', label: 'A few times a week' },
  { value: 'daily', label: 'Daily' },
  { value: 'several_daily', label: 'Several times a day' },
];

export const SHARE_OPTIONS = [
  { value: 'nextOfKin', label: 'Next of kin' },
  { value: 'carer', label: 'Carer' },
  { value: 'gp', label: 'GP' },
];

export const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];

export const DAY_LABELS = {
  monday: 'Monday',
  tuesday: 'Tuesday',
  wednesday: 'Wednesday',
  thursday: 'Thursday',
  friday: 'Friday',
  saturday: 'Saturday',
  sunday: 'Sunday',
};

export const DAY_LABELS_SHORT = {
  monday: 'Mon',
  tuesday: 'Tue',
  wednesday: 'Wed',
  thursday: 'Thu',
  friday: 'Fri',
  saturday: 'Sat',
  sunday: 'Sun',
};

export const HOBBY_SUGGESTIONS = [
  'Gardening', 'Walking', 'Reading', 'Knitting', 'Bowls', 'Golf',
  'Cards & games', 'Cooking', 'Music', 'Television', 'Church / community', 'Volunteering',
];

export const HEALTH_CONDITION_SUGGESTIONS = [
  'Diabetes', 'Heart condition', 'High blood pressure', 'Arthritis', 'Osteoporosis',
  'Dementia / memory issues', 'Previous falls', 'Vision impairment', 'Hearing impairment',
  'Respiratory condition',
];

export const ACTIVITY_SUGGESTIONS = [
  'Morning walk', 'Grocery shopping', 'Visitor', 'Gardening', 'Bowls club',
  'Church', 'Seniors group', 'Physio / appointment', 'Lunch out',
];

export function labelFor(options, value) {
  const found = options.find((o) => o.value === value);
  return found ? found.label : value || '—';
}

// ---------------------------------------------------------------------------
// Draft = working form state while onboarding is in progress. Times and dates
// stay as raw text so people can type freely; buildProfile() normalizes them.

export function createEmptyDraft() {
  return {
    demographics: { name: '', sex: null, dobText: '', livingSituation: null },
    contacts: {
      nextOfKin: { name: '', relationship: '', phone: '' },
      secondary: { enabled: false, name: '', relationship: '', phone: '' },
      gp: { enabled: false, name: '', relationship: 'GP / care provider', phone: '' },
    },
    sleep: { typicalWake: '', typicalSleep: '', napPattern: '' },
    weeklyRoutine: {
      monday: [], tuesday: [], wednesday: [], thursday: [],
      friday: [], saturday: [], sunday: [],
    },
    hobbies: [],
    mobilityLevel: null,
    lifestyle: {
      diet: null,
      dietNote: '',
      smoking: { status: false, frequency: null },
      alcohol: { status: false, frequency: null },
    },
    healthContext: [],
    medicationCount: null,
    consent: { monitoringConsent: false, sharedWith: ['nextOfKin'] },
  };
}

// ---------------------------------------------------------------------------
// Per-step validation. Returns an object of fieldKey → message; empty = valid.

function contactErrors(contact, prefix, errors) {
  if (!contact.name.trim()) errors[`${prefix}.name`] = 'Name is required.';
  if (!contact.relationship.trim()) errors[`${prefix}.relationship`] = 'Relationship is required.';
  if (!normalizePhone(contact.phone)) errors[`${prefix}.phone`] = 'Enter a valid phone number, e.g. 0412 345 678.';
}

export function validateStep(stepKey, draft) {
  const errors = {};
  switch (stepKey) {
    case 'demographics': {
      const d = draft.demographics;
      if (!d.name.trim()) errors.name = 'Full name is required.';
      if (!d.sex) errors.sex = 'Please select an option.';
      if (!parseDob(d.dobText)) errors.dob = 'Enter date of birth as DD/MM/YYYY.';
      if (!d.livingSituation) errors.livingSituation = 'Please select an option.';
      break;
    }
    case 'contacts': {
      contactErrors(draft.contacts.nextOfKin, 'nextOfKin', errors);
      if (draft.contacts.secondary.enabled) contactErrors(draft.contacts.secondary, 'secondary', errors);
      if (draft.contacts.gp.enabled) contactErrors(draft.contacts.gp, 'gp', errors);
      break;
    }
    case 'sleep': {
      if (!parseTime(draft.sleep.typicalWake)) errors.typicalWake = 'Enter a time, e.g. 7:30 am.';
      if (!parseTime(draft.sleep.typicalSleep)) errors.typicalSleep = 'Enter a time, e.g. 9:30 pm.';
      break;
    }
    case 'hobbiesMobility': {
      if (!draft.mobilityLevel) errors.mobilityLevel = 'Please select a mobility level.';
      break;
    }
    case 'lifestyle': {
      if (draft.lifestyle.smoking.status && !draft.lifestyle.smoking.frequency) {
        errors.smokingFrequency = 'Please select how often.';
      }
      if (draft.lifestyle.alcohol.status && !draft.lifestyle.alcohol.frequency) {
        errors.alcoholFrequency = 'Please select how often.';
      }
      break;
    }
    case 'consent': {
      if (!draft.consent.monitoringConsent) {
        errors.monitoringConsent = 'Consent to monitoring is required to use the wearable.';
      }
      break;
    }
    default:
      break;
  }
  return errors;
}

// ---------------------------------------------------------------------------
// Draft → UserBaselineProfile (the persisted contract object).

function cleanContact(contact, isPrimary) {
  return {
    name: contact.name.trim(),
    relationship: contact.relationship.trim(),
    phone: normalizePhone(contact.phone),
    isPrimary,
  };
}

export function buildProfile(draft) {
  const contacts = [cleanContact(draft.contacts.nextOfKin, true)];
  if (draft.contacts.secondary.enabled) contacts.push(cleanContact(draft.contacts.secondary, false));
  if (draft.contacts.gp.enabled) contacts.push(cleanContact(draft.contacts.gp, false));

  const weeklyRoutine = {};
  for (const day of DAYS) {
    weeklyRoutine[day] = draft.weeklyRoutine[day].map((e) => ({
      activity: e.activity,
      expectedTime: e.expectedTime,
      expectedDuration: e.expectedDuration,
    }));
  }

  const { diet, dietNote, smoking, alcohol } = draft.lifestyle;
  const dietParts = [];
  if (diet && diet !== 'other') dietParts.push(labelFor(DIET_OPTIONS, diet));
  if (dietNote.trim()) dietParts.push(dietNote.trim());

  return {
    schemaVersion: PROFILE_SCHEMA_VERSION,
    demographics: {
      name: draft.demographics.name.trim(),
      sex: draft.demographics.sex,
      dob: parseDob(draft.demographics.dobText),
      livingSituation: draft.demographics.livingSituation,
    },
    emergencyContacts: contacts,
    sleep: {
      typicalWake: parseTime(draft.sleep.typicalWake),
      typicalSleep: parseTime(draft.sleep.typicalSleep),
      napPattern: draft.sleep.napPattern.trim() || null,
    },
    weeklyRoutine,
    hobbies: [...draft.hobbies],
    mobilityLevel: draft.mobilityLevel,
    lifestyle: {
      diet: dietParts.join(' — ') || null,
      smoking: { status: smoking.status, frequency: smoking.status ? smoking.frequency : null },
      alcohol: { status: alcohol.status, frequency: alcohol.status ? alcohol.frequency : null },
    },
    healthContext: [...draft.healthContext],
    medicationCount: draft.medicationCount,
    consent: {
      monitoringConsent: draft.consent.monitoringConsent,
      sharedWith: [...draft.consent.sharedWith],
    },
    deviceId: null,
    completedAt: new Date().toISOString(),
  };
}

// Final gate before persisting — belt-and-braces on top of per-step validation,
// and the same checks later modules can run before trusting a stored profile.
export function validateProfile(profile) {
  const problems = [];
  if (!profile.demographics?.name) problems.push('Missing name.');
  if (!profile.demographics?.sex) problems.push('Missing sex.');
  if (!profile.demographics?.dob) problems.push('Missing or invalid date of birth.');
  if (!profile.demographics?.livingSituation) problems.push('Missing living situation.');

  const primaries = (profile.emergencyContacts || []).filter((c) => c.isPrimary);
  if (primaries.length !== 1 || !primaries[0].phone) {
    problems.push('Exactly one primary emergency contact with a valid phone number is required.');
  }

  if (!profile.sleep?.typicalWake || !profile.sleep?.typicalSleep) problems.push('Missing sleep baseline.');
  if (!profile.mobilityLevel) problems.push('Missing mobility level.');
  for (const day of DAYS) {
    if (!Array.isArray(profile.weeklyRoutine?.[day])) problems.push(`Missing routine list for ${day}.`);
  }
  if (!profile.consent?.monitoringConsent) problems.push('Monitoring consent has not been given.');
  return problems;
}

// ---------------------------------------------------------------------------
// Profile → draft, so a saved profile can be re-opened and edited.

export function profileToDraft(profile) {
  const draft = createEmptyDraft();

  draft.demographics = {
    name: profile.demographics.name,
    sex: profile.demographics.sex,
    dobText: formatDob(profile.demographics.dob),
    livingSituation: profile.demographics.livingSituation,
  };

  const contacts = profile.emergencyContacts || [];
  const primary = contacts.find((c) => c.isPrimary) || contacts[0];
  const others = contacts.filter((c) => c !== primary);
  if (primary) draft.contacts.nextOfKin = { name: primary.name, relationship: primary.relationship, phone: primary.phone };
  const gp = others.find((c) => /gp|doctor|care provider|clinic/i.test(c.relationship));
  const secondary = others.find((c) => c !== gp);
  if (secondary) draft.contacts.secondary = { enabled: true, name: secondary.name, relationship: secondary.relationship, phone: secondary.phone };
  if (gp) draft.contacts.gp = { enabled: true, name: gp.name, relationship: gp.relationship, phone: gp.phone };

  draft.sleep = {
    typicalWake: formatTime(profile.sleep.typicalWake),
    typicalSleep: formatTime(profile.sleep.typicalSleep),
    napPattern: profile.sleep.napPattern || '',
  };

  for (const day of DAYS) {
    draft.weeklyRoutine[day] = (profile.weeklyRoutine?.[day] || []).map((e) => ({ ...e }));
  }

  draft.hobbies = [...(profile.hobbies || [])];
  draft.mobilityLevel = profile.mobilityLevel;
  draft.lifestyle = {
    diet: null,
    dietNote: profile.lifestyle?.diet || '',
    smoking: { ...(profile.lifestyle?.smoking || { status: false, frequency: null }) },
    alcohol: { ...(profile.lifestyle?.alcohol || { status: false, frequency: null }) },
  };
  draft.healthContext = [...(profile.healthContext || [])];
  draft.medicationCount = profile.medicationCount ?? null;
  draft.consent = {
    monitoringConsent: !!profile.consent?.monitoringConsent,
    sharedWith: [...(profile.consent?.sharedWith || [])],
  };

  return draft;
}
