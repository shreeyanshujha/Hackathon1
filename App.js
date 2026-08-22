import React, { useEffect, useState } from 'react';
import { View, ActivityIndicator, StyleSheet } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import OnboardingFlow from './src/onboarding/OnboardingFlow';
import ProfileHomeScreen from './src/screens/ProfileHomeScreen';
import { loadProfile, loadDraft, clearProfile, clearDraft } from './src/storage/profileStore';
import { createEmptyDraft, profileToDraft } from './src/model/profile';
import { colors } from './src/theme';

export default function App() {
  const [ready, setReady] = useState(false);
  const [profile, setProfile] = useState(null);
  const [onboarding, setOnboarding] = useState(false);
  const [initialDraft, setInitialDraft] = useState(null);

  useEffect(() => {
    (async () => {
      const saved = await loadProfile();
      if (saved) {
        setProfile(saved);
      } else {
        // Resume a half-finished setup if one was left behind.
        const draft = await loadDraft();
        setInitialDraft(draft || createEmptyDraft());
        setOnboarding(true);
      }
      setReady(true);
    })();
  }, []);

  const handleComplete = (newProfile) => {
    setProfile(newProfile);
    setOnboarding(false);
  };

  const handleEdit = () => {
    setInitialDraft(profileToDraft(profile));
    setOnboarding(true);
  };

  const handleReset = async () => {
    await clearProfile();
    await clearDraft();
    setProfile(null);
    setInitialDraft(createEmptyDraft());
    setOnboarding(true);
  };

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
      {!ready ? (
        <View style={styles.loading}>
          <ActivityIndicator size="large" color={colors.primary} />
        </View>
      ) : onboarding ? (
        <OnboardingFlow initialDraft={initialDraft} onComplete={handleComplete} />
      ) : (
        <ProfileHomeScreen profile={profile} onEdit={handleEdit} onReset={handleReset} />
      )}
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  loading: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.bg },
});
