// Persistence for the baseline profile and in-progress onboarding draft.
// Later modules (anomaly detection, AI-agent calling) read the profile through
// loadProfile() only — swap AsyncStorage for Firebase/Supabase here without
// touching any consumer.

import AsyncStorage from '@react-native-async-storage/async-storage';

const PROFILE_KEY = 'baseline_profile_v1';
const DRAFT_KEY = 'onboarding_draft_v1';

async function readJson(key) {
  try {
    const raw = await AsyncStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function saveProfile(profile) {
  await AsyncStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
}

export function loadProfile() {
  return readJson(PROFILE_KEY);
}

export async function clearProfile() {
  await AsyncStorage.removeItem(PROFILE_KEY);
}

export async function saveDraft(draft) {
  await AsyncStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
}

export function loadDraft() {
  return readJson(DRAFT_KEY);
}

export async function clearDraft() {
  await AsyncStorage.removeItem(DRAFT_KEY);
}
