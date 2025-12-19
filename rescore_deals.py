import asyncio
from sqlalchemy import text
from app.database import async_session
from app.services.scoring_service import score_deal

async def rescore_all_deals():
    async with async_session() as session:
        result = await session.execute(text('''
            SELECT d.id, d.title, d.brand, d.price, d.original_price, 
                   d.discount_percent, d.category, d.sizes_available, d.color
            FROM deals d
            ORDER BY d.id DESC
            LIMIT 200
        '''))
        deals = result.fetchall()
        
        print(f'Re-scoring {len(deals)} deals...')
        
        for deal in deals:
            deal_id = deal[0]
            deal_data = {
                'product_name': deal[1],
                'brand': deal[2],
                'sale_price': deal[3],
                'original_price': deal[4],
                'discount_percent': deal[5] or 0,
                'category': deal[6] or 'default',
                'sizes_available': deal[7] or [],
                'color': deal[8],
            }
            
            try:
                score_result = await score_deal(deal_data, None)
                
                await session.execute(text('''
                    UPDATE deal_scores 
                    SET flip_score = :flip_score,
                        recommended_price = :recommended_price,
                        estimated_sell_days = :estimated_sell_days,
                        recommended_action = :recommended_action,
                        confidence = :confidence,
                        updated_at = NOW()
                    WHERE deal_id = :deal_id
                '''), {
                    'deal_id': deal_id,
                    'flip_score': score_result['flip_score'],
                    'recommended_price': score_result.get('recommended_price'),
                    'estimated_sell_days': score_result.get('estimated_sell_days'),
                    'recommended_action': score_result.get('recommended_action'),
                    'confidence': score_result.get('confidence'),
                })
                print(f'  Deal {deal_id}: price={score_result.get("recommended_price")}, days={score_result.get("estimated_sell_days")}')
            except Exception as e:
                print(f'  Deal {deal_id}: ERROR - {e}')
        
        await session.commit()
        print('Done!')

asyncio.run(rescore_all_deals())
