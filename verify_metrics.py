import pandas as pd
import numpy as np

def clean_percentage(val):
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val) / 100.0 if val > 1.0 else float(val)
    if isinstance(val, str):
        val = val.replace('%', '').strip()
        if not val:
            return 0.0
        return float(val) / 100.0
    return 0.0

def test_xp():
    # Mock data
    data = {
        'PLAYER_NAME': ['Starter', 'Impact Sub', 'Bench Warmer', 'Uncertain'],
        'REAL_SALE_PRICE': [10_000_000, 5_000_000, 2_000_000, 8_000_000],
        'AVG_POINTS_MOMENTUM': [10.0, 10.0, 10.0, 10.0],
        'COMUNIATE_STARTER': ['100%', '0%', '0', 0.5], # Mixed formats
        'COMUNIATE_SUPPLENT': [0, '100%', '0%', 0.5],
        'IS_AVAILABLE': [True, True, True, True]
    }
    df = pd.DataFrame(data)
    
    # Clean percentages
    df['COMUNIATE_STARTER'] = df['COMUNIATE_STARTER'].apply(clean_percentage)
    df['COMUNIATE_SUPPLENT'] = df['COMUNIATE_SUPPLENT'].apply(clean_percentage)
    
    # Calculate xP
    # Formula: Momentum * (Starter + (Sub * 0.8))
    df['EXPECTED_POINTS'] = df['AVG_POINTS_MOMENTUM'] * (df['COMUNIATE_STARTER'] + (df['COMUNIATE_SUPPLENT'] * 0.8))
    
    # Calculate Cost per xP
    df['COST_PER_XP'] = 0.0
    mask = (df['IS_AVAILABLE']) & (df['EXPECTED_POINTS'] > 0)
    df.loc[mask, 'COST_PER_XP'] = (df.loc[mask, 'REAL_SALE_PRICE'] / 1_000_000) / df.loc[mask, 'EXPECTED_POINTS']

    print("xP Verification:")
    print(df[['PLAYER_NAME', 'EXPECTED_POINTS', 'COST_PER_XP']])
    
    # Assertions
    # Starter: 10 * (1.0 + 0) = 10.0
    assert df.loc[df['PLAYER_NAME'] == 'Starter', 'EXPECTED_POINTS'].iloc[0] == 10.0
    assert df.loc[df['PLAYER_NAME'] == 'Starter', 'COST_PER_XP'].iloc[0] == 1.0 # 10M / 10xp
    
    # Impact Sub: 10 * (0 + (1.0 * 0.8)) = 8.0
    assert df.loc[df['PLAYER_NAME'] == 'Impact Sub', 'EXPECTED_POINTS'].iloc[0] == 8.0
    assert df.loc[df['PLAYER_NAME'] == 'Impact Sub', 'COST_PER_XP'].iloc[0] == 0.625 # 5M / 8xp
    
    # Bench Warmer: 10 * (0 + 0) = 0.0
    assert df.loc[df['PLAYER_NAME'] == 'Bench Warmer', 'EXPECTED_POINTS'].iloc[0] == 0.0
    assert df.loc[df['PLAYER_NAME'] == 'Bench Warmer', 'COST_PER_XP'].iloc[0] == 0.0
    
    # Uncertain: 10 * (0.5 + (0.5 * 0.8)) = 10 * (0.5 + 0.4) = 9.0
    assert df.loc[df['PLAYER_NAME'] == 'Uncertain', 'EXPECTED_POINTS'].iloc[0] == 9.0

    print("\n✅ Expected Points (xP) logic verified successfully!")

if __name__ == "__main__":
    test_xp()
