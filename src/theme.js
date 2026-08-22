// Shared visual language. Large type and high contrast on purpose — the flow may
// be completed by the older person themselves, not just a family member.

export const colors = {
  bg: '#F4F7F9',
  card: '#FFFFFF',
  text: '#17242D',
  subtext: '#5A6B76',
  border: '#D9E2E8',
  primary: '#1F6E8C',
  primaryDark: '#175A73',
  primarySoft: '#E3F0F5',
  danger: '#B3372A',
  dangerSoft: '#FAE7E4',
  success: '#2E7D32',
  successSoft: '#E6F2E6',
  warning: '#8A6D1A',
  warningSoft: '#FBF3D9',
};

export const type = {
  title: { fontSize: 26, fontWeight: '700', color: colors.text },
  subtitle: { fontSize: 16, color: colors.subtext, lineHeight: 23 },
  label: { fontSize: 16, fontWeight: '600', color: colors.text },
  body: { fontSize: 16, color: colors.text, lineHeight: 23 },
  small: { fontSize: 13, color: colors.subtext, lineHeight: 18 },
};
